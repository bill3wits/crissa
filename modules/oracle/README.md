# Crissa oracle module (optional)

The clock's face check and the door feed. The light Wall install skips it; nothing here is needed to
watch her, talk to her, or connect an agent. **Skeleton**: the pieces run and fall back, the identity
check is not yet proved on a labelled set.

- **FF, the face check**: is the person at the screen the enrolled user. A Mac always runs FF and is
  never offered Secure mode; a Linux install may be offered it.
- **Voice is only a backup for FF**: it may confirm the user when the face check saw nobody. It never
  overrules a face that does not match.
- **Door feed**: a face appearing in the part of the picture that shows the door
  (`CRISSA_ORACLE_DOOR="x0,y0,x1,y1"`, fractions of the frame; unset = no door feed).
- **Beam microphone**: decorative for now. With none plugged in the clock shows `no beam sig` in tiny
  type. Direction is coming soon.

Everything falls back quietly. No camera, no face engine or no beam microphone is a small mark on the
clock, never an error. With `--no-camera` (the launcher passes `CRISSA_NO_CAMERA=1`; `CRISSA_ORACLE_CAMERA=off`
does the same) the oracle opens no capture device at all and its state reads `camera_off: true`.

## Models, fetched on first use (nothing ships in this module)

- MediaPipe Face Landmarker (Apache-2.0): finds faces and their landmarks.
- AuraFace v1 recognition model (Apache-2.0, `fal/AuraFace-v1`, `glintr100.onnx` only), optional:
  `CRISSA_ORACLE_AURAFACE=1`. Without it the oracle says whether a face is there, not whose.

## Run

    pip install mediapipe opencv-python-headless numpy       # plus onnxruntime for AuraFace
    python3 modules/oracle/scripts/crissa-oracle.py --install
    python3 modules/oracle/scripts/crissa-oracle.py &
    export CRISSA_ORACLE=http://127.0.0.1:8197                # the Wall's status marks read this

The enrolled face is a short list of numbers in `~/.crissa/oracle/user-face.json` on this machine.
No picture is kept. Delete the file to forget the user.
