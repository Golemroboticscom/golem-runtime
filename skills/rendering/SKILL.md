---
name: rendering
description: Actually run the render engine on a defined scene — headless, no display — and produce raw images/frames. Use when a scene is set up (camera/material/lighting done) and it's time to actually execute the render engine — a single view, a multi-view set, a turntable frame sequence, or a data chart.
---

# Running a Render

## Purpose

Perform the actual capture through a render engine, after the scene is already set up (skill `scene-setup`) and on a mesh that has already been exported (skill `export`). This stage is execution — no real-time camera/material guessing, and no graphical display (the server is headless).

## When It Runs

After a scene is set up and verified. Not before — running a render on an unverified scene is the fastest way to waste compute time on a black image.

## Steps

1. **Headless environment — no display, no GPU.** The server has no display and no graphics card. Two engines, two ways to run:
   - **pyvista**: `pv.OFF_SCREEN = True` in the code, and `off_screen=True` on the `Plotter`.
     If there is a virtual-display problem — wrap in `xvfb-run -a python3 tools/render.py`.
   - **Blender**: explicit background mode (`-b`), and the engine forced to CYCLES on CPU
     (`scene.render.engine = "CYCLES"`, `scene.cycles.device = "CPU"`) —
     EEVEE fails without a real display (missing `libEGL.so.1`).
     ```
     blender -b -P tools/blender_render.py
     ```

2. **Static multi-view.** Loop over the camera positions defined in the scene stage
   (for example iso/front/side), render each one separately, a separate PNG file per angle.
   Useful for engineering documentation — three fixed views that repeat exactly every time.

3. **Turntable sequence.** Loop over evenly-spaced angles around the model's center
   (for example 36 steps of 10 degrees), one frame per angle, numbered file names
   (`f000.png`…`f035.png`) in a dedicated frames directory. **The frames are an intermediate product,
   not the final product** — assembling them into a video happens in the next stage (skill
   `presentation-output-preparation`).

4. **Single photorealistic render.** A single render call on CYCLES-CPU. The number
   of samples (`samples`) is a quality-versus-time trade-off — low (~64) for a draft, higher
   for a final hero image. Output resolution set in advance (for example 1400×1050).
   This is significantly slower than pyvista — reserve time, do not run it at the last minute before delivery.

5. **Data charts — not a 3D engine.** For charts (for example a BOM breakdown by mass/cost)
   the engine is matplotlib with a non-interactive backend (`matplotlib.use("Agg")`),
   not pyvista/Blender. A useful template: a pair of horizontal bars (mass alongside cost),
   with `bar_label` to show the exact value on each bar, and a neutral, consistent color palette.

6. **Sanity check after every run.** The output file exists and its size is non-zero; not fully black
   (classic sign: the camera cut the model out of the image — go back to skill `scene-setup`,
   check `clip_end`/camera distance) and not blank white (sign: the mesh was not found/wrong path —
   go back to skill `export`). Do not pass a product to the presentation stage without this check.

## Expected Output

Raw image file(s) in the output directory (a single view, a multi-view set, a numbered frame
sequence, or a data chart) — passed a basic sanity check, ready for assembly/delivery
in skill `presentation-output-preparation`.

## Owner

Rendering

---
*Registered in the skills catalog `_meta/skills.csv` — the source of truth for skills (scope · owner · status · confidence · creation/update dates · call_count · purpose); coverage enforced by R-004.*
