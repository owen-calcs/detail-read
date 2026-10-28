# Detail Read

Upload a steel connection drawing (PDF), let OCR read the metric callouts, convert them to imperial, and solve the
matching connection calculator on Calcs.com **connectionshub** over MCP. The governing load, governing check and
utilization come back to the browser, with a link to the live calculation.

```
PDF ─► render pages ─► Tesseract OCR (0°, 90°, 270°) + PDF text layer
    ─► parse callouts (W/HSS/L/C sections, plates, bolts, rebar, welds, loads, elevations, dims)
    ─► metric → imperial (nearest AISC section, mm → ft-in, kN → kip, kN·m → kip·ft, M20 → 3/4")
    ─► group callouts into connection details around their design-load notes
    ─► rank connectionshub calculators per detail (AISC 360-22)
    ─► MCP: create_project → validate_inputs → create_and_solve_calculation
    ─► governing load / combination / check / utilization ─► UI
```

## Run

Requirements: Python 3.13+, [uv](https://docs.astral.sh/uv/), Tesseract (`brew install tesseract`).

```bash
uv sync
uv run detail-read
```

Open http://127.0.0.1:8000 and drop in a PDF.

### connectionshub key

The backend talks to `https://api.calcs.com/mcp` with the connectionshub key. It uses `CONNECTIONSHUB_API_KEY` if set,
otherwise the key already configured for the `connectionshub` MCP server in `~/.claude.json`. A general Calcs.com key
will not do: it sees a different calculator catalog.

## How it decides

- **Any PDF.** Each page is rendered at 200 dpi and OCR'd upright and rotated both ways, so vertical dimension text is
  read too. Embedded PDF text is merged in when it exists. Scanned, vector-only and mixed sets all work.
- **Metric → imperial.** Sections map to the nearest AISC shape from the same catalog the calculators accept
  (W200x36 → W8X24, HSS152x152x9.5 → HSS6X6X.375, L102x76x9.5 → L4X3X3/8). Plate thicknesses snap to 1/16".
  Common OCR slips are repaired (`W610x24l`, `HSS1152x…`, `IG0KN`, `7/4"` bolts).
- **Detecting the drawing.** Loads written in CSA notation (`Vf`, `Tf`, `Cf`, `Mf`, `Hf`) seed a detail. The members,
  plates and bolts nearest each note belong to it, and the wording around it (shear tab, brace, chord, splice, base
  plate, girt, moment) picks the calculator and preset. A page with no load notes is treated as one detail.
- **Loads.** Drawing loads are factored (CSA *f* subscript), so they are sent as LRFD factored loads. Every
  value is editable in the UI before solving.
- **Governing load.** This is the calculator's governing load combination with its demand when it reports one, and
  otherwise the largest demand. It is shown alongside the governing check (`summaryCheckReferenceId`) and utilization.

Every solve creates a calculation in one "Detail Read (PDF OCR)" project, whose ID is cached in `.detail-read/state.json`.

## Layout

| File | Role |
| --- | --- |
| `src/detail_read/ocr.py` | Page rendering, Tesseract OCR, PDF text layer |
| `src/detail_read/extract.py` | Callout parsing and metric → imperial conversion |
| `src/detail_read/sections.py` | Nearest AISC section lookup (`data/sections_aisc.json`) |
| `src/detail_read/detect.py` | Detail grouping and calculator ranking |
| `src/detail_read/calc.py` | Calculator inputs, solve, governing-load summary |
| `src/detail_read/mcp_client.py` | connectionshub MCP client |
| `src/detail_read/app.py` | FastAPI backend |
| `src/detail_read/static/` | Frontend |

## Limits

- connectionshub has no WF-to-WF or WF base plate calculator. Those details fall back to the nearest fit and say so.
- Details drawn only with angles or channels get no automatic calculator; pick one in the dropdown.
- OCR can still misread digits (5950 read as 5450). Check the conversion table before trusting a result.
- Jobs live in memory, so re-upload after a server restart.

## Tests

```bash
uv run pytest
```
