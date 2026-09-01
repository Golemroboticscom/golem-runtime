---
name: export
description: Exporting geometry from a CAD file to a triangle surface (mesh) that rendering engines can read. Use when a render, turntable or presentation image needs a fresh STL/mesh pulled from the current CAD/design source before any camera, material or lighting work can start.
---

# ייצוא גיאומטריה (CAD → mesh)

## מטרה

Before anything can be captured — a triangle surface that the rendering engine understands is needed. The export stage
takes the CAD file (the geometric source of truth) and produces a mesh file (usually STL),
a disposable, computationally inexpensive file that serves as the input for the rest of the rendering
pipeline. The exported file is a **disposable derivative** — do not edit it by hand or retain it as a source.

## מתי מופעל

At the beginning of every rendering pipeline, and after every geometric change in the source (CAD) that requires an updated render.
If the existing mesh already matches the current CAD state (there has been no change) — there is no need to export it again.

## צעדים

1. **Opening the CAD file headlessly.** Through the CAD tool's console runner (for example,
   `freecadcmd`), without a graphical interface, so that it can also run on a server without a screen and without an interactive session:
   ```
   freecadcmd tools/export_stl.py
   ```
   Example from the project: `/root/renobot/tools/export_stl.py` opens
   `robot_base_green_arm.FCStd` and exports to `/tmp/robot.stl`.

2. **Filtering only the correct objects.** Go through all document objects and take only
   what actually needs to appear in the render:
   - It has a valid `Shape`,
   - `Shape.Volume` is above a zero threshold (not flat/two-dimensional auxiliary geometry),
   - `Visibility=True` (objects intentionally hidden in the CAD file — auxiliary sketches,
     intermediate versions, dimensional guides — should not appear in the render).
   Incorrect filtering here is the most common source of problems: a mesh with 0 triangles (everything was filtered out)
   or a mesh with unnecessary auxiliary geometry (the `Visibility` filter was forgotten).

3. **Converting to a mesh with quality appropriate for the purpose.** `MeshPart.meshFromShape` with
   `LinearDeflection` and `AngularDeflection` control the trade-off between surface accuracy, file size,
   and rendering time. A coarser value (larger deflection) is suitable for a sequence of frames (turntable —
   36 renders using the same mesh, so speed matters); a finer value is suitable for a single hero
   image in which every detail is visible.

4. **Combining into a single mesh and writing to a temporary file.** Join all filtered objects
   into one mesh (`addMesh`) and write it to `/tmp/robot.stl` or an equivalent temporary path —
   **not** into the code directory or the source directory.

5. **Immediate sanity check.** Print the number of exported objects and the number of
   triangles (`CountFacets`). An object count of 0 or a triangle count of 0 means that the filtering in step
   2 accidentally rejected everything — stop and fix it before proceeding to the next stage (scene setup/rendering),
   otherwise time will be wasted rendering an empty mesh.

6. **One-way operation.** The exported mesh is discarded/replaced on every rerun. If an updated render
   is needed — return to step 1 using the current CAD, rather than editing the existing STL.

## פלט צפוי

A single mesh file (STL) at a temporary path, with a valid object and triangle count (>0),
matching 1:1 the visible objects in the current CAD file — ready as input for scene setup.

## בעלים

Rendering

---
*Recorded in the `_meta/skills.csv` skills catalog — the source of truth for skills (scope · owner · status · confidence · creation/update dates · call_count · purpose); coverage is enforced by R-004.*
