import sys
sys.path[:0] = ["/srv/runtime/lib", "/srv/runtime/src"]
from golem_runtime.cli import main

MISSION = (
 "Design the SIMPLEST possible robot that drives forward and backward and carries a payload of at least 1 kg.\n"
 "\n"
 "The robot's own mass must not exceed 50 kg. That is a CEILING, not a target: lighter is better, and a design "
 "that comes in far under it is a better design, not a wasted allowance.\n"
 "\n"
 "It must drive at least 100 m continuously on a hard indoor floor, including a controlled stop, and operate for "
 "at least 30 minutes on one charge.\n"
 "\n"
 "EVERY performance claim must be backed by a calculation - torque against mass, energy against runtime, stability "
 "margin against the payload's height. A claim without a calculation counts as UNPROVEN and must be labelled so.\n"
 "\n"
 "Simplicity is the requirement, and it is MEASURED: part count. An off-the-shelf part beats a manufactured one; "
 "fewer parts beat more parts. Every part must be obtainable, named with a supplier and a price.\n"
 "\n"
 "Nothing beyond this was asked for - no arm, no sideways travel, no manipulation, no autonomy beyond driving "
 "forward and backward. An unrequested addition is a design failure, not initiative."
)
CAPABILITIES = "drives forward; drives backward; carries a payload of at least 1 kg; comes to a controlled stop"
RID = "simple-carrier-3"

sys.exit(main([
 "run", "design-robot", "--run-id", RID, "--transport", "bridge", "--gate", "telegram", "--store", "sqlite",
 "--param", f"mission_request={MISSION}",
 "--param", f"capability_list={CAPABILITIES}",
 "--resume", "--param", f"product_path=/srv/runtime/artifacts/{RID}",
]))
