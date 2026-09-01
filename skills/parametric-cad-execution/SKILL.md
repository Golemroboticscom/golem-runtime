---
name: parametric-cad-execution
description: Running a geometry/parameter change in a parametric CAD model (CSV source of truth + FreeCAD macro) and producing an updated mass/center-of-gravity report. Use when changing a CAD parameter, tube size, component mass, or geometry, or when regenerating the model / mass report after an edit.
---

# ביצוע CAD פרמטרי

## מטרה

Change geometry or a parameter in a CAD model without breaking the principle on which it is built:
**A single human-readable parameter file is the only source of truth, and the macro only reads it.**
In this project, this is `robot_params.csv` (the source) + `robot_base_macro.FCMacro`
(the actual build, headless via `freecadcmd`). The pattern is generic: any parametric model
built this way — a value file + a build script that reads from it — is executed using the same procedure.

## מתי מופעל

- Change of dimension, material, component mass, or item count in the model.
- Before running FEA or rendering, when there is a suspicion that the model was not updated since the last change to the parameter file.
- After receiving an actual component specification (from a part number/datasheet/RFQ) that needs to replace an estimate.

## צעדים

1. **Back up before touching a binary file.** CAD files (`.FCStd`) are binary —
   git keeps a version but does not show a readable diff. Make sure `git status` is clean before starting,
   so you can safely revert.
2. **Never edit geometry directly in the macro.** The value always goes into the parameter file
   (`robot_params.csv`) — columns `key,value,unit,note`. The macro reads it through
   `load_params()`/`g(key)` and uses `DEFAULTS` only as a fallback, not as a source.
   Changing a default without changing the corresponding CSV row = a silent contradiction.
3. **Headless rebuild.**
   ```
   freecadcmd robot_base_macro.FCMacro
   ```
   Runs without an interface, rereads the parameters, builds the geometry, and prints to stdout/the terminal a full report: weight and cost by group (G1–G8), center of gravity x/y/z, load per wheel,
   and stability ratio (base/CoGh). This is also the fast verification path — if the numbers do not move
   in the expected direction, the calculation in the macro or the row in the CSV is wrong.
4. **Inspect objects** — `inspect_fcstd.py` / `debug_fcstd.py` print the
   object trees, visibility (`Visibility`), and proxies of every component. Useful when something
   disappears from the render or export without an apparent error. `fix_visibility.py` fixes
   components that remained hidden after a rebuild.
5. **Export for the next stage.** `tools/export_stl.py` converts all visible solids
   into a single STL (`/tmp/robot.stl`) for rendering (`tools/render.py`,
   `xvfb-run -a python3 tools/render.py`). Model-specific (such as an arm) — see
   the pattern in `export_green_arm.py`: it runs a model-specific macro inside `exec` in a clean namespace,
   and does not edit the main macro.
6. **Run FEA again if you touched structural geometry** (tube diameter/wall, material, mass) —
   see skill `run-fea`. A geometry change without rerunning = old SF on a new model.
7. **Sync the change outward.** The parameter file is not the end of the chain — there are derived tables
   (BOM, requirements specification) that also need to be updated. See skill `design-change-sync`
   for the complete continuation of the process, including commit and change logging.

## פלט צפוי

An up-to-date CAD model that agrees with the parameter file in 100% of the fields; a fresh mass/CoG report
printed to stdout/the terminal; an exported STL (if relevant for the next stage); the only diff committed
to git is in the readable parameter file (the CSV) — not a guess about what changed in the binary.

## בעלים

Engineering Lead

---
*Registered in the skills catalog `_meta/skills.csv` — the source of truth for skills (scope · owner · status · confidence · creation/update dates · call_count · purpose); coverage is enforced by R-004.*
