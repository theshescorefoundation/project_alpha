"""CQI dataset registry: one load() for CSV today, tables / lake paths tomorrow.

    from cqi_datasets import Datasets
    ds = Datasets()                          # reads ../config/datasets.yaml
    df = ds.load("visits")                   # pandas DataFrame from csv/visits.csv
    ds = Datasets(engine="spark", spark=spark, source="table")   # Databricks: everything from tables
    ds.load("visits")                        # Spark DataFrame, canonical column names + types

Sources: csv | table | path | auto (= table if configured, else path, else csv).
Source precedence for each dataset:  load(..., source=)  >  Datasets(source=)  >  env CQI_SOURCE
                                     >  dataset `source` in yaml  >  defaults.source
"""
import os
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PANDAS_TYPES = {"string": "string", "int": "Int64", "double": "float64", "bool": "boolean"}
SPARK_TYPES = {"string": "string", "int": "int", "double": "double", "date": "date", "timestamp": "timestamp", "bool": "boolean"}
TRUE = {"true", "1", "yes", "y", "t"}
FALSE = {"false", "0", "no", "n", "f"}


class DatasetError(Exception):
    pass


class Datasets:
    def __init__(self, config=os.path.join(HERE, "..", "config", "datasets.yaml"), engine="pandas", spark=None, source=None):
        self.config_path = os.path.abspath(config)
        self.cfg = yaml.safe_load(open(self.config_path))
        self.engine = engine
        self.spark = spark
        self.source = source or os.environ.get("CQI_SOURCE")
        if engine == "spark" and spark is None:
            raise DatasetError("engine='spark' needs spark=spark")
        base = os.path.dirname(self.config_path)
        self.csv_dir = os.path.join(base, self.cfg["defaults"].get("csv_dir", "csv"))
        self.log = []

    # ------------------------------------------------------------------ info
    def names(self):
        return list(self.cfg["datasets"])

    def spec(self, name):
        try:
            return self.cfg["datasets"][name]
        except KeyError:
            raise DatasetError(f"unknown dataset '{name}'. Known: {', '.join(self.names())}")

    def resolve_source(self, name, source=None):
        s = self.spec(name)
        src = source or self.source or s.get("source") or self.cfg["defaults"]["source"]
        if src == "auto":   # production: table if configured, else lake path, else csv
            src = "table" if s.get("table") else "path" if s.get("path") else "csv"
        return src

    def describe(self):
        """Table of datasets, where each one is read from, and whether it exists."""
        rows = []
        for n in self.names():
            s, src = self.spec(n), self.resolve_source(n)
            loc = {"csv": os.path.join(self.csv_dir, s.get("csv", f"{n}.csv")), "table": s.get("table"), "path": s.get("path")}.get(src)
            exists = os.path.exists(loc) if src == "csv" else None
            rows.append({"dataset": n, "source": src, "location": loc, "required": s.get("required", False),
                         "exists": exists, "columns": len(s["columns"])})
        return rows

    # ------------------------------------------------------------------ load
    def load(self, name, source=None, required=None):
        """Return the dataset with canonical columns and types, or None if optional and missing."""
        s = self.spec(name)
        src = self.resolve_source(name, source)
        req = s.get("required", False) if required is None else required
        try:
            df = self._read(name, s, src)
        except (FileNotFoundError, DatasetError) as e:
            if req:
                raise DatasetError(f"required dataset '{name}' not available from {src}: {e}")
            self.log.append(f"[skip] {name}: optional, not available from {src}")
            return None
        except Exception as e:  # Spark AnalysisException etc.
            if req:
                raise
            self.log.append(f"[skip] {name}: optional, {e.__class__.__name__} from {src}")
            return None
        return self._conform(name, s, df, src)

    def _read(self, name, s, src):
        if src == "csv":
            path = os.path.join(self.csv_dir, s.get("csv", f"{name}.csv"))
            if not os.path.exists(path):
                raise FileNotFoundError(path)
            if self.engine == "pandas":
                import pandas as pd
                return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
            return self.spark.read.option("header", True).option("multiLine", True).option("escape", '"').csv(path)
        if self.engine != "spark":
            raise DatasetError(f"source '{src}' needs engine='spark' (run on Databricks) - or export it with snapshot_to_csv()")
        if src == "table":
            if not s.get("table"):
                raise DatasetError(f"no `table` configured for {name}")
            return self.spark.table(s["table"])
        if src == "path":
            if not s.get("path"):
                raise DatasetError(f"no `path` configured for {name}")
            return self.spark.read.format(s.get("format", self.cfg["defaults"].get("format", "delta"))).load(s["path"])
        raise DatasetError(f"unknown source '{src}' for {name}")

    def _conform(self, name, s, df, src):
        """Rename (table/path only), add missing columns as null, cast types, keep canonical columns only."""
        rename = s.get("rename", {}) if src != "csv" else {}
        cols = s["columns"]
        if self.engine == "pandas":
            import pandas as pd
            have = {c.lower(): c for c in df.columns}
            out = pd.DataFrame(index=df.index)
            missing = []
            for canon, typ in cols.items():
                srcc = rename.get(canon, canon)
                if srcc.lower() in have:
                    col = df[have[srcc.lower()]]
                else:
                    col = pd.Series([None] * len(df), index=df.index, dtype="object"); missing.append(srcc)
                out[canon] = _cast_pandas(col, typ)
            if missing:
                self.log.append(f"[warn] {name}: missing columns filled with null -> {missing}")
            return out.reset_index(drop=True)
        from pyspark.sql import functions as F
        have = {c.lower(): c for c in df.columns}
        sel, missing = [], []
        for canon, typ in cols.items():
            srcc = rename.get(canon, canon)
            if srcc.lower() in have:
                c = F.col(f"`{have[srcc.lower()]}`")
                if typ == "bool":
                    lc = F.lower(F.trim(c.cast("string")))
                    c = F.when(lc.isin(*TRUE), F.lit(True)).when(lc.isin(*FALSE), F.lit(False))
                elif typ == "date":
                    c = F.to_date(c)
                else:
                    c = c.cast(SPARK_TYPES[typ])
            else:
                c = F.lit(None).cast(SPARK_TYPES[typ]); missing.append(srcc)
            sel.append(c.alias(canon))
        if missing:
            self.log.append(f"[warn] {name}: missing columns filled with null -> {missing}")
        return df.select(*sel)

    # ------------------------------------------------------------------ checks
    def check(self, name, df=None):
        """Light data-quality check: row count, null rate on key columns, duplicate grain."""
        df = self.load(name) if df is None else df
        if df is None:
            return {"dataset": name, "status": "missing"}
        grain = [g.strip() for g in self.spec(name).get("grain", "").split(",") if g.strip()]
        if self.engine == "pandas":
            n = len(df)
            nulls = {c: int(df[c].isna().sum()) for c in grain if c in df}
            dups = int(df.duplicated(subset=[g for g in grain if g in df]).sum()) if grain else 0
        else:
            from pyspark.sql import functions as F
            n = df.count()
            nulls = df.select([F.sum(F.col(c).isNull().cast("int")).alias(c) for c in grain]).first().asDict() if grain else {}
            dups = n - df.dropDuplicates(grain).count() if grain else 0
        return {"dataset": name, "rows": n, "null_keys": {k: v for k, v in nulls.items() if v}, "duplicate_grain_rows": dups,
                "status": "ok" if n and not dups and not any(nulls.values()) else "review"}

    # ------------------------------------------------------------------ table -> csv
    def snapshot_to_csv(self, name, out_dir=None, keys_df=None, source="auto", limit=None):
        """Databricks: pull a dataset from its table/path, optionally filter to a cohort (keys_df with
        patient_id[, episode_id]), and write a single CSV in canonical columns to the csv folder.
        Use this to build small, realistic dev datasets. Write PHI only to approved locations."""
        if self.engine != "spark":
            raise DatasetError("snapshot_to_csv runs with engine='spark'")
        df = self.load(name, source=source, required=True)
        if keys_df is not None:
            on = [k for k in ("patient_id", "episode_id") if k in df.columns and k in keys_df.columns]
            df = df.join(keys_df.select(*on).distinct(), on, "inner")
        if limit:
            df = df.limit(limit)
        out_dir = out_dir or self.csv_dir
        target = os.path.join(out_dir, self.spec(name).get("csv", f"{name}.csv"))
        df.toPandas().to_csv(target, index=False)
        return target


class CohortPandasView:
    """Databricks bridge: load each dataset with Spark, keep only cohort rows, hand pandas to build_payloads.
    One implementation of the booklet logic (build_payloads.py) for local CSVs and for Lighthouse tables.

        view = CohortPandasView(spark_ds, cohort_keys_sdf)      # Spark DF with patient_id, episode_id
        build_payloads.run(view, AS_OF, OUT_DIR, percentiles=...)
    Suits pilot-sized cohorts (hundreds to a few thousand episodes)."""

    def __init__(self, spark_ds, keys_sdf):
        self.ds = spark_ds
        self.keys = keys_sdf.select("patient_id", "episode_id").distinct().cache()
        self.log = spark_ds.log

    def names(self):
        return self.ds.names()

    def resolve_source(self, name, source=None):
        return self.ds.resolve_source(name, source)

    def spec(self, name):
        return self.ds.spec(name)

    def load(self, name, source=None, required=None):
        sdf = self.ds.load(name, source=source, required=required)
        if sdf is None:
            return None
        if "episode_id" in sdf.columns:
            sdf = sdf.join(self.keys, ["patient_id", "episode_id"], "left_semi")
        elif "patient_id" in sdf.columns:
            sdf = sdf.join(self.keys.select("patient_id").distinct(), ["patient_id"], "left_semi")
        pdf = sdf.toPandas()
        for col, typ in self.spec(name)["columns"].items():
            pdf[col] = _cast_pandas(pdf[col], typ)
        return pdf


def _cast_pandas(col, typ):
    import pandas as pd
    if typ == "date":
        return pd.to_datetime(col, errors="coerce").dt.normalize()
    if typ == "timestamp":
        return pd.to_datetime(col, errors="coerce")
    if typ == "bool":
        s = col.astype("string").str.strip().str.lower()
        return s.map(lambda v: True if v in TRUE else False if v in FALSE else None).astype("boolean")
    if typ in ("int", "double"):
        return pd.to_numeric(col, errors="coerce").astype(PANDAS_TYPES[typ])
    return col.astype("string")


if __name__ == "__main__":
    ds = Datasets()
    print(f"{'dataset':<14}{'source':<7}{'req':<5}{'exists':<8}location")
    for r in ds.describe():
        print(f"{r['dataset']:<14}{r['source']:<7}{str(r['required'])[0]:<5}{str(r['exists']):<8}{r['location']}")
    print()
    for n in ds.names():
        print(ds.check(n))
    print("\n".join(ds.log))
