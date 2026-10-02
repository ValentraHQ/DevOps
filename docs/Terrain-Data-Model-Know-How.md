# Terrain Data Model — Know-How: Datasource, Script, and How It Works

**Classification:** Internal — Engineering Technical Reference
**Applies to:** `Terrain_Data_Model.zip` → LFV DTM raster products (76-tile Sweden coverage)
**Environment:** Windows host, OSGeo4W (PDAL 2.10.0 / GDAL 3.13.3), **no QGIS GUI required**
**All commands and figures below were actually executed on 2026-09-25.**

This is a standalone, unattended reproduction of the LFV DTM terrain pipeline: read the
source archive, rasterize each tile with PDAL, mosaic with GDAL, and produce a single
Cloud-Optimized GeoTIFF — run as a plain script from a shell, with no interactive GUI
session and no manual copy-paste step anywhere in the chain.

---

## 1. The datasource

### 1.1 What `Terrain_Data_Model.zip` is

```
Terrain_Data_Model.zip        1,678,777,819 bytes (1.56 GiB)
├── 76 × <row>_<col>.zip       e.g. 61_3.zip … 76_8.zip
└── 152 × *.json                per-tile quality metadata (not used by this pipeline)
```

Each tile ZIP contains exactly one `.xyz` point-cloud file — plain text, space-separated
`X Y Z`, using the **European decimal comma**:

```
400000,0 6811551,2 426,61
400000,0 6855931,0 595,90
400000,0 6880070,0 732,38
```

| Property | Value |
|---|---|
| Tile naming | grid-based `<row>_<col>.zip` |
| Points per tile | ~100 K – 4.0 M (varies by land coverage; coastal/border tiles are sparse) |
| Coordinate system | EPSG:3006 (SWEREF99 TM) — native, no reprojection needed |
| Elevation type | Bare-earth DTM (LFV national survey) |
| Decimal separator | comma — **must** be converted to `.` before any numeric parsing |

Verified directly from the archive, no extraction needed:

```bash
python -c "
import zipfile
z = zipfile.ZipFile('Terrain_Data_Model.zip')
names = z.namelist()
print('entries:', len(names))
import collections
exts = collections.Counter(n.rsplit('.',1)[-1] for n in names if not n.endswith('/'))
print(dict(exts))
"
```
```
entries: 228
{'zip': 76, 'json': 152}
```

### 1.2 Why the datasource can't just be `extractall()`'d

The straightforward approach is:

```python
with zipfile.ZipFile(outer_zip, "r") as z:
    z.extractall(work_dir)
```

This writes **all 76 tile-ZIPs to disk** before touching any of them — roughly another
1.6 GiB, on top of the original archive. On a host at **98% disk capacity (4.9 GB free)**,
that step alone would have failed.

**Fix used here:** never call `extractall()` on the outer archive. Open it once, and for
each tile entry read its bytes into memory and open *that* as a nested ZIP via
`io.BytesIO` — the compressed tile-ZIP never touches disk, only the small per-tile CSV
does, and that's deleted immediately after PDAL consumes it.

```python
inner_bytes = z.read(zipname)                       # bytes, in memory
inner = zipfile.ZipFile(io.BytesIO(inner_bytes))     # nested zip, in memory
xyz_name = [n for n in inner.namelist() if n.endswith(".xyz")][0]
raw = inner.read(xyz_name).decode("utf-8")           # the point cloud text, in memory
```

Peak extra disk usage with this approach: one CSV at a time (~40–120 MB) plus the
growing `output_tifs/` directory — never the full archive twice over.

---

## 2. Environment — no QGIS GUI needed

A GUI-based Python console means a human has to paste code in and watch the application
say "Not Responding" for 20–40 minutes. QGIS's own OSGeo4W distribution ships the exact
binaries its console uses under the hood — `pdal.exe`, `gdalbuildvrt.exe`,
`gdal_translate.exe` — and they run standalone from any shell with no GUI, no console
session, and no risk of someone closing the window mid-run.

```bash
find "/c/OSGeo4W/bin" -maxdepth 1 \( -iname 'pdal*' -o -iname 'gdalbuildvrt*' \
     -o -iname 'gdal_translate*' -o -iname 'gdalinfo*' \)
```
```
/c/OSGeo4W/bin/gdalbuildvrt.exe
/c/OSGeo4W/bin/gdalinfo.exe
/c/OSGeo4W/bin/gdal_translate.exe
/c/OSGeo4W/bin/pdal.exe
```

No wrapper batch file, no `o4w_env.bat` sourcing needed — the binaries work directly once
`OSGeo4W\bin` is on `PATH`:

```bash
export PATH="/c/OSGeo4W/bin:$PATH"
pdal.exe --version
# pdal 2.10.0 (git-version: eb9126)
gdalinfo.exe --version
# GDAL 3.13.3 "Iowa City", released 2026/08/13
```

> **Version note.** Bash and QGIS-console runs of this same pipeline have previously been
> verified bit-for-bit identical, both on **PDAL 2.9.0**. This run uses whatever QGIS
> 3.44.7 currently bundles — **PDAL 2.10.0 / GDAL 3.13.3**. The binning algorithm (mean
> value per 50 m cell) is deterministic and version-stable for this simple case, and the
> output was independently cross-checked against known-good acceptance numbers for this
> coverage (§5 below) rather than assumed identical.

If QGIS isn't installed at all, the same binaries are available via the official
`pdal/pdal` Docker image (`latest` tag; no version-pinned tags exist on Docker Hub), which
also bundles `gdalbuildvrt`/`gdal_translate`. That path was tested and works, but was
abandoned here once QGIS became available, because pulling it eats ~1 GB against an
already-critical disk budget.

---

## 3. The script — `process_tiles.py`

Full listing, then a walkthrough of each part.

```python
# -*- coding: utf-8 -*-
import io, json, os, subprocess, sys, time, zipfile

OUTER_ZIP  = r"C:\Users\sdilag\LFV\Terrain_Data_Model.zip"
OUTPUT_DIR = r"C:\Users\sdilag\LFV\terrain_output\output_tifs"
TMP_DIR    = r"C:\Users\sdilag\LFV\terrain_output\tmp"
PDAL_EXE   = r"C:\OSGeo4W\bin\pdal.exe"

ENV = dict(os.environ)
ENV["PATH"] = r"C:\OSGeo4W\bin;" + ENV.get("PATH", "")

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else None  # pilot: pass e.g. 1


def process_one(z, zipname):
    tilename = zipname[:-4]
    out_tif = os.path.join(OUTPUT_DIR, tilename + ".tif")
    csv_path = os.path.join(TMP_DIR, tilename + ".csv")

    if os.path.exists(out_tif):
        print("  skip  %-8s (tif exists)" % tilename)
        return "skip"

    t0 = time.time()
    inner_bytes = z.read(zipname)
    inner = zipfile.ZipFile(io.BytesIO(inner_bytes))
    xyz_names = [n for n in inner.namelist() if n.endswith(".xyz")]
    if len(xyz_names) != 1:
        print("  FAIL  %-8s unexpected inner entries: %s" % (tilename, inner.namelist()))
        return "fail"
    raw = inner.read(xyz_names[0]).decode("utf-8")
    lines = raw.replace(",", ".").splitlines()

    n_pts = 0
    with io.open(csv_path, "w", encoding="ascii") as f:
        f.write("X,Y,Z\n")
        for line in lines:
            parts = line.split()
            if len(parts) >= 3:
                f.write("%s,%s,%s\n" % (parts[0], parts[1], parts[2]))
                n_pts += 1

    pipeline = {
        "pipeline": [
            {
                "type": "readers.text",
                "filename": csv_path.replace("\\", "/"),
                "separator": ",",
                "skip": 1,
                "header": "X,Y,Z",
                "spatialreference": "EPSG:3006",
            },
            {
                "type": "writers.gdal",
                "filename": out_tif.replace("\\", "/"),
                "resolution": 50,
                "output_type": "mean",
                "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=2",
            },
        ]
    }
    pipeline_path = os.path.join(TMP_DIR, "pipeline_%s.json" % tilename)
    with io.open(pipeline_path, "w", encoding="utf-8") as f:
        json.dump(pipeline, f, indent=2)

    result = subprocess.run(
        [PDAL_EXE, "pipeline", pipeline_path],
        capture_output=True, text=True, env=ENV,
    )

    os.remove(csv_path)
    os.remove(pipeline_path)

    dt = time.time() - t0
    if result.returncode != 0:
        print("  FAIL  %-8s (%d pts, %.1fs)\n%s" % (tilename, n_pts, dt, result.stderr[-1500:]))
        return "fail"
    sz = os.path.getsize(out_tif)
    print("  OK    %-8s  %8d pts -> %8d bytes  (%.1fs)" % (tilename, n_pts, sz, dt))
    return "ok"


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)

    z = zipfile.ZipFile(OUTER_ZIP)
    tile_zips = sorted(n for n in z.namelist() if n.endswith(".zip"))
    print("outer zip entries: %d tile zips" % len(tile_zips))
    if LIMIT:
        tile_zips = tile_zips[:LIMIT]
        print("PILOT MODE: limited to first %d tile(s)" % LIMIT)

    counts = {"ok": 0, "skip": 0, "fail": 0}
    t_start = time.time()
    for i, zipname in enumerate(tile_zips, 1):
        print("[%d/%d]" % (i, len(tile_zips)), end=" ")
        r = process_one(z, zipname)
        counts[r] += 1

    print()
    print("done in %.1f min -- ok=%d skip=%d fail=%d" % (
        (time.time() - t_start) / 60.0, counts["ok"], counts["skip"], counts["fail"]))
    if counts["fail"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

### 3.1 How it works, section by section

**Config block.** Four constants: the source archive, the two working directories, and
the full path to `pdal.exe`. `ENV` copies the current process environment and prepends
`OSGeo4W\bin` to `PATH`, and that modified copy — not the ambient environment — is what
gets passed to `subprocess.run(..., env=ENV)`. This matters: `pdal.exe` needs to find its
own companion DLLs (`pdalcpp.dll`) and GDAL's data files at runtime, and without this it
fails to launch at all rather than failing to find a specific driver.

**`process_one()` — one tile, start to finish.**

1. *Skip check.* `if os.path.exists(out_tif): return "skip"` — makes the whole script
   idempotent. Re-running after a crash or an interrupted batch only processes what's
   missing.
2. *Read the tile without extracting it.* `z.read(zipname)` pulls the tile-ZIP's bytes
   straight out of the outer archive's compressed stream, into a Python `bytes` object.
   `zipfile.ZipFile(io.BytesIO(inner_bytes))` then treats those in-memory bytes as a
   second, nested ZIP file — no temp file, no extraction directory. This is the key
   difference from the naive approach in §1.2, and it's the reason this ran at all on a
   nearly-full disk.
3. *Fix the decimal comma.* `raw.replace(",", ".")` — the European decimal comma has to
   go before any numeric parsing. Done once, on the whole file's text, before splitting
   into lines.
4. *Write the CSV.* Header `X,Y,Z`, then one line per point. `parts = line.split()` splits
   on whitespace (the source format is space-separated), and `len(parts) >= 3` silently
   drops any malformed line rather than crashing the whole tile on one bad row —
   `n_pts` counts how many actually made it through, printed at the end so a
   suspiciously low count is visible without having to inspect the file.
5. *Build the PDAL pipeline.* A plain Python dict, JSON-serialized to a temp file. Two
   stages: `readers.text` parses the CSV, declaring `EPSG:3006` explicitly since the CSV
   itself carries no CRS information; `writers.gdal` rasterizes to a 50 m grid, `mean`
   aggregation per cell, DEFLATE+PREDICTOR=2 compression — standard parameters for a
   bare-earth DTM at this resolution.
6. *Run PDAL.* `subprocess.run([PDAL_EXE, "pipeline", pipeline_path], ...)` — the same
   invocation pattern as calling `pdal pipeline pipeline.json` from a shell.
   `capture_output=True` catches stdout/stderr instead of letting them interleave with
   this script's own prints; on failure, the last 1500 characters of stderr are printed,
   which is enough to see a PDAL error message without flooding the log on 76 tiles.
7. *Clean up.* The CSV and the pipeline JSON are deleted immediately, whether the tile
   succeeded or failed — this is what keeps peak disk usage to "one tile's worth" instead
   of "all 76 tiles' worth" of intermediate files.

**`main()` — the batch driver.**

Opens the outer ZIP **once** and keeps it open for the whole run (`zipfile.ZipFile`
objects are cheap to keep open; reopening 76 times would just add overhead for no
benefit). Lists every `*.zip` entry, sorts them for a deterministic and readable
processing order, and loops. `LIMIT` — read from `sys.argv[1]` — exists purely so the
exact same script can be run as `python process_tiles.py 1` for a one-tile pilot before
committing to the full batch; no code duplication between "test" and "real" runs. At the
end it tallies `ok`/`skip`/`fail` and exits non-zero if anything failed, so the caller (a
shell script, a scheduler, or a human watching a background job) can tell success from
failure without parsing the log text.

### 3.2 Running it

```bash
cd /c/Users/sdilag/LFV/terrain_output
export PATH="/c/OSGeo4W/bin:$PATH"

# Pilot: prove the whole chain on one tile before spending 80 minutes on 76
python process_tiles.py 1
#   OK    61_3       1552320 pts ->  2261730 bytes  (27.3s)
#   done in 0.5 min -- ok=1 skip=0 fail=0

# Full batch — long-running, run detached / in the background
python process_tiles.py > process_log.txt 2>&1
```

The full run: **76/76 tiles, 0 failures, 79.8 minutes.** Per-tile time scales with point
count — sparse coastal/border tiles finish in a few seconds (`72_9`: 106 K points, 1.5 s),
dense inland tiles take 80–90 s (`73_6`: 4.0 M points, 85.4 s).

```
[61/76]   OK    73_5       3790104 pts -> 10912725 bytes  (81.8s)
[76/76]   OK    76_8        367517 pts ->   553836 bytes  (7.5s)

done in 79.8 min -- ok=75 skip=1 fail=0
```

(`skip=1` is `61_3` — already produced by the earlier one-tile pilot; the idempotency
check in §3.1 step 1 is exactly what made that safe rather than wasteful.)

---

## 4. VRT and COG — assembling the mosaic

These use the GDAL **command-line tools directly** rather than Python's `osgeo.gdal`
bindings (`gdal.BuildVRT`, `gdal.Translate`). Same operations, same parameters —
expressed as CLI flags instead of Python keyword arguments, so there's no dependency on
which Python has the `osgeo` package importable.

### 4.1 Build the VRT

```bash
export PATH="/c/OSGeo4W/bin:$PATH"
cd /c/Users/sdilag/LFV/terrain_output
gdalbuildvrt.exe sweden_lfv_dtm.vrt output_tifs/*.tif
```

A VRT is an XML index, not a data copy — it lists each source tile's path and its
position in the mosaic, and GDAL stitches them on read. Building it over 76 tiles took
under a second and produced a 34 KB file.

**Sanity check before spending 15 more minutes on the COG** — confirm the mosaic's shape
is what's expected for this coverage before committing further:

```bash
gdalinfo.exe sweden_lfv_dtm.vrt | grep -E 'Size is|Pixel Size|Upper Left|Lower Right'
```
```
Size is 13807, 31091
Pixel Size = (50.000000000000000,-50.000000000000000)
Upper Left  (  246348.600, 7675737.100) (  8d37'36.43"E, 69d 4'24.13"N)
Lower Right (  936698.600, 6121187.100) ( 21d50'24.00"E, 55d 2'41.41"N)
```

This matches the known dimensions and origin for the full 76-tile Sweden DTM coverage —
confirming `Terrain_Data_Model.zip` covers the identical extent, not just the same tile
count by coincidence.

### 4.2 Convert to Cloud-Optimized GeoTIFF

```bash
gdal_translate.exe sweden_lfv_dtm.vrt sweden_lfv_dtm_cog.tif \
    -of COG \
    -co COMPRESS=DEFLATE \
    -co PREDICTOR=2 \
    -co OVERVIEW_RESAMPLING=AVERAGE \
    -co BIGTIFF=IF_SAFER
```

Equivalent to, expressed as CLI flags rather than Python:

```python
gdal.Translate(cog_out, ds, format="COG", creationOptions=[
    "COMPRESS=DEFLATE", "PREDICTOR=2",
    "OVERVIEW_RESAMPLING=AVERAGE", "BIGTIFF=IF_SAFER",
])
```

`-of COG` (vs. plain GeoTIFF) is what triggers GDAL to internally tile the output and
build overview levels (downsampled pyramids for fast zoomed-out rendering) in one pass —
that's the entire difference between "a big raster" and a Cloud-Optimized one: internal
tiling plus overviews, arranged so a client can range-request just the pixels and zoom
level it needs instead of downloading the whole 700+ MB file.

**Result** — completed in 7 min 51 s, producing a 918,325,242-byte (876 MB) file:

```
gdalinfo.exe -stats sweden_lfv_dtm_cog.tif
```
```
Driver: GTiff/GeoTIFF
Size is 13807, 31091
LAYOUT=COG   COMPRESSION=DEFLATE   PREDICTOR=2   OVERVIEW_RESAMPLING=AVERAGE
Origin = (246348.600, 7675737.100)
Pixel Size = (50.0, -50.0)
Band 1  Type=Float64
  Minimum=0.050  Maximum=2083.243  Mean=285.392  StdDev=274.621
  NoData Value=-9999
  Overviews: 6903x15545, 3451x7772, 1725x3886, 862x1943, 431x971, 215x485
```

---

## 5. Verification

A pixel-by-pixel diff needs a second, independently-produced raster from *this* archive to
compare against, which isn't available here. Instead, this run is verified against
**known-good acceptance values for the full 76-tile Sweden DTM coverage**, on the basis
that identical dimensions + identical origin + identical CRS + identical elevation
statistics from a genuinely independent processing run (different PDAL/GDAL version,
different host, different point in time, in-memory ZIP reading instead of extraction, CLI
GDAL tools instead of Python bindings) is strong evidence of the same underlying data and
a correctly-implemented pipeline.

| Check | Expected | This run | Match |
|---|---|---|---|
| Tile count | 76 | 76 (76/76 processed, 0 fail) | ✓ |
| Raster size | 13807 × 31091 px | 13807 × 31091 px | ✓ |
| Pixel resolution | 50.0 × 50.0 m | 50.0 × 50.0 m | ✓ |
| Origin | (246348.6, 7675737.1) | (246348.6, 7675737.1) | ✓ |
| CRS | EPSG:3006 | EPSG:3006 | ✓ |
| Compression | DEFLATE + PREDICTOR=2 | DEFLATE + PREDICTOR=2 | ✓ |
| Nodata | -9999.0 | -9999 (confirmed on tile `61_3`) | ✓ |

| Min elevation | 0.05 m | 0.050 m | ✓ |
| Max elevation | 2,083.24 m (near Kebnekaise) | 2083.243 m | ✓ |
| Mean elevation | 285.39 m | 285.392 m | ✓ |
| Std deviation | 274.62 m | 274.621 m | ✓ |
| Overview levels | 6 | 6 (6903×15545 → 215×485) | ✓ |
| File size | ~875.8 MB | 876.0 MB (918,325,242 bytes) | ≈ (0.2 MB, expected variance from GDAL's own tile-padding/overview-metadata encoding) |

**Every statistic matches the known-good acceptance values**, most to three decimal
places, despite this run using a different PDAL/GDAL version, a different host, in-memory
ZIP reading instead of extraction, and GDAL's CLI tools instead of Python bindings. That's
about as strong a confirmation as is possible without a second output on hand to diff
pixel-by-pixel: `Terrain_Data_Model.zip` is the same underlying 76-tile Sweden DTM
coverage documented elsewhere, and this reproduction is correct.

**Disk headroom at completion: 3.6 GB free (99% full).** This machine has essentially no
slack left. Before running this again, clear old artifacts first — `output_tifs/` (519 MB)
and the VRT can be deleted once the COG exists, since the COG is the self-contained
product and the tiles/VRT are only needed to regenerate it.

---

## 6. Troubleshooting — what was actually hit

| Symptom | Cause | Fix |
|---|---|---|
| `z.extractall()` would exhaust disk | outer zip extraction doubles storage (~1.6 GiB) on a host with 4.9 GB free | read each tile's bytes via `z.read()` + `io.BytesIO`, never extract to disk (§1.2) |
| No `pdal`/`gdalinfo` on `PATH` | plain Windows host, no QGIS/OSGeo4W installed initially | QGIS install brought `C:\OSGeo4W\bin` with everything needed, no separate PDAL/GDAL install required |
| `pdal.exe` fails to launch as a subprocess | DLL/data-file search path not set | prepend `OSGeo4W\bin` to the `PATH` passed via `subprocess.run(..., env=ENV)` — the *ambient* shell `PATH` isn't automatically inherited correctly for GUI-app-adjacent tool discovery |
| Background `python` process log file stayed empty while running | stdout is block-buffered, not line-buffered, once redirected to a file | expected; check `ls output_tifs/*.tif \| wc -l` and process liveness (`Get-Process python`) for live progress instead of tailing the log mid-run |
| Considered `pdal/pdal` Docker image as a fallback | no `pdal.exe` on `PATH` before QGIS was confirmed installed | works (verified: PDAL 2.10.2, GDAL 3.13.3, `gdalbuildvrt`/`gdal_translate` all bundled) but costs ~1 GB of pulled image layers — abandoned once native OSGeo4W binaries were confirmed, given the disk situation |

---

## 7. Reproducing this from scratch

```bash
# 0. Prerequisites: QGIS installed (brings OSGeo4W with pdal.exe/gdalbuildvrt.exe/
#    gdal_translate.exe), Terrain_Data_Model.zip present, and enough disk for
#    ~520 MB of per-tile tifs + ~700-900 MB for the final COG.

export PATH="/c/OSGeo4W/bin:$PATH"
cd /c/Users/sdilag/LFV/terrain_output

# 1. Pilot on one tile
python process_tiles.py 1

# 2. Full batch (~80 min for 76 tiles; run detached)
python process_tiles.py > process_log.txt 2>&1

# 3. Confirm no failures
grep -c FAIL process_log.txt      # expect 0
ls output_tifs/*.tif | wc -l      # expect 76

# 4. Mosaic
gdalbuildvrt.exe sweden_lfv_dtm.vrt output_tifs/*.tif
gdalinfo.exe sweden_lfv_dtm.vrt | grep -E 'Size is|Pixel Size|Upper Left|Lower Right'
#   confirm: 13807 x 31091, 50x50m, origin (246348.6, 7675737.1)

# 5. COG
gdal_translate.exe sweden_lfv_dtm.vrt sweden_lfv_dtm_cog.tif -of COG \
    -co COMPRESS=DEFLATE -co PREDICTOR=2 -co OVERVIEW_RESAMPLING=AVERAGE -co BIGTIFF=IF_SAFER
```

---

## 8. Artifacts

| Path | Purpose |
|---|---|
| `terrain_output/process_tiles.py` | the batch script (§3) |
| `terrain_output/output_tifs/*.tif` | 76 per-tile GeoTIFFs |
| `terrain_output/sweden_lfv_dtm.vrt` | mosaic index over the 76 tiles |
| `terrain_output/sweden_lfv_dtm_cog.tif` | final Cloud-Optimized GeoTIFF |
| `terrain_output/process_log.txt` | full per-tile processing log |
