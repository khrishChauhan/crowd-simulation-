# /data/cameras

Place up to 5 pre-recorded CCTV clips here to enable the OpenCV
motion-proxy CV pipeline (see `backend/app/cv_pipeline.py`):

```
data/cameras/camera_01.mp4   -> CAM-01
data/cameras/camera_02.mp4   -> CAM-02
data/cameras/camera_03.mp4   -> CAM-03
data/cameras/camera_04.mp4   -> CAM-04
data/cameras/camera_05.mp4   -> CAM-05
```

If a file is missing (the default state of this repo — no video files are
bundled), CrowdShield AI does **not** crash or degrade the demo. That
camera automatically falls back to **SIMULATION FALLBACK** mode, which
derives a coherent synthetic observation from the linked checkpoint's live
simulation state. The dashboard clearly labels which mode each camera is
in (`CV_MOTION_PROXY` vs `SIMULATION_FALLBACK` vs `OFFLINE`).

This is intentional: the architecture is identical whether the input is a
pre-recorded file, a folder of files, or (in production) a live RTSP / IP
camera stream — only `Camera._read_frame()` in `cv_pipeline.py` would
change to pull from `cv2.VideoCapture("rtsp://...")` instead of a file
path.
