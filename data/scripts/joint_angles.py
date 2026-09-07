"""Hip / knee / ankle flexion angles from adjacent MVN segment quaternions.

Reads segment quaternions directly from the hub (needs "Publish quaternions"
enabled in the Xsens UDP tab). For each of the six major lower-limb joints,
computes the relative rotation between parent and child segments and reports
the sagittal-plane flexion angle in degrees.

Method:
    1. q_rel = conjugate(q_parent) * q_child     (rotation of child in parent frame)
    2. Convert q_rel to Euler angles using SciPy's Rotation.from_quat with
       ``seq='ZYX'`` (extrinsic Tait-Bryan). The Y component of that
       decomposition is the flexion/extension angle when segment frames follow
       the MVN default (X-forward, Y-up, Z-right).
    3. Sign convention: positive = flexion (hip forward, knee bent,
       ankle dorsiflexed), negative = extension. If your MVN configuration
       uses a different segment frame convention and the numbers look wrong,
       change ``FLEXION_EULER_INDEX`` to 0 (X) or 2 (Z) and re-Rescan.

Segment indexing (MVN spec, 0-based loop index used by ``XsensUdpSource``):
    Pelvis = 0
    RightUpperLeg = 15,  RightLowerLeg = 16,  RightFoot = 17
    LeftUpperLeg  = 19,  LeftLowerLeg  = 20,  LeftFoot  = 21

Joints:
    right_hip   = pelvis    →  RightUpperLeg
    right_knee  = RUL       →  RightLowerLeg
    right_ankle = RLL       →  RightFoot
    left_hip    = pelvis    →  LeftUpperLeg
    left_knee   = LUL       →  LeftLowerLeg
    left_ankle  = LLL       →  LeftFoot

References:
    * Grood, E. S., & Suntay, W. J. (1983). "A joint coordinate system for the
      clinical description of three-dimensional motions: application to the
      knee." Journal of Biomechanical Engineering, 105(2), 136–144.
      (The gold-standard convention; we approximate here with an Euler
      decomposition per MVN's segment frames.)
    * Movella Xsens MVN User Manual — segment coordinate systems.
    * SciPy Rotation: https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.transform.Rotation.html
"""

from __future__ import annotations

import math

from scipy.spatial.transform import Rotation

from gaitlab.scripts.api import AnalysisScript, ScriptOutput

# Joint parent → child (both are segment loop indices, 0-based).
_JOINTS = [
    ("right_hip",   0, 15),
    ("right_knee",  15, 16),
    ("right_ankle", 16, 17),
    ("left_hip",    0, 19),
    ("left_knee",   19, 20),
    ("left_ankle",  20, 21),
]

# 0=X (roll), 1=Y (pitch), 2=Z (yaw) in ZYX Tait-Bryan Euler.
# MVN default frame → sagittal flexion ≈ Y-axis rotation.
FLEXION_EULER_INDEX = 1


class JointAngles(AnalysisScript):
    id = "biomech.joint_angles"
    display_name = "Joint angles — hip / knee / ankle (MVN quaternions)"
    default_rate_hz = 60.0

    inputs: list = []  # reads quaternion channels directly via self.hub

    outputs = [
        ScriptOutput("right_hip_deg", unit="deg", description="Right hip flexion (positive) / extension (negative)."),
        ScriptOutput("right_knee_deg", unit="deg", description="Right knee flexion."),
        ScriptOutput("right_ankle_deg", unit="deg", description="Right ankle dorsi- (positive) / plantar-flexion."),
        ScriptOutput("left_hip_deg", unit="deg"),
        ScriptOutput("left_knee_deg", unit="deg"),
        ScriptOutput("left_ankle_deg", unit="deg"),
    ]

    def setup(self) -> None:
        # Fail fast if quaternion channels aren't available; the error surfaces
        # in the Scripts panel status line so the researcher knows to enable
        # "Publish quaternions" in the Xsens UDP tab.
        if self.hub is None:
            raise RuntimeError("Runtime did not inject hub reference.")
        if self.hub.try_get("xsens.seg00.qw") is None:
            raise RuntimeError(
                "Quaternion channels not published. Enable 'Publish quaternions' "
                "in the Connections → Xsens UDP tab, then re-Enable this script."
            )

    def _read_quat(self, seg_idx: int) -> tuple[float, float, float, float]:
        """Return (qw, qx, qy, qz) — the MVN convention. Returns identity if missing."""
        prefix = f"xsens.seg{seg_idx:02d}"
        qw = self.hub.try_get(f"{prefix}.qw")
        qx = self.hub.try_get(f"{prefix}.qx")
        qy = self.hub.try_get(f"{prefix}.qy")
        qz = self.hub.try_get(f"{prefix}.qz")
        if qw is None or qx is None or qy is None or qz is None:
            return 1.0, 0.0, 0.0, 0.0
        return float(qw), float(qx), float(qy), float(qz)

    def _joint_angle_deg(self, parent_idx: int, child_idx: int) -> float:
        pw, px, py, pz = self._read_quat(parent_idx)
        cw, cx, cy, cz = self._read_quat(child_idx)
        # SciPy Rotation.from_quat expects (x, y, z, w) order.
        r_parent = Rotation.from_quat([px, py, pz, pw])
        r_child = Rotation.from_quat([cx, cy, cz, cw])
        r_rel = r_parent.inv() * r_child
        euler = r_rel.as_euler("ZYX", degrees=True)
        # euler is [z, y, x]; FLEXION_EULER_INDEX addresses [x, y, z], so remap.
        remap = [euler[2], euler[1], euler[0]]  # -> [x, y, z]
        return float(remap[FLEXION_EULER_INDEX])

    def on_sample(self, ts: float, values: dict[str, float]) -> dict[str, float]:
        return {
            name + "_deg": self._joint_angle_deg(parent, child)
            for name, parent, child in _JOINTS
        }
