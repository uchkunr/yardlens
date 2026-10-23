# Sack model: the part still to do

**Status (2026-10-14).** Everything around counting is built and tested: loading sessions,
number plates, the dashboard, history and Excel export, load balancing. What is missing is a
detector that knows what a sack is. YOLO's stock COCO classes have none, so a loading bay can
open a session for a truck but counts no cargo. The plan is to train a model on footage from the
customer's own bay camera as soon as that footage exists.

## Already tried, don't repeat

All on the demo clips below, with the RTX 4070 machine:

| Approach | Result |
| --- | --- |
| Stock `yolo26n.pt` | No sack class. The green trailer counts as `truck` on at most 9% of frames. |
| YOLOE-26s, text prompts (`sack`, `bag`, `white bag`, `bag of grain`, `sandbag`) | Best score on a sack 0.15. `pillow` scores 0.4–0.5, but only on sacks lying still. |
| YOLOE-26s, text prompt `trailer` | Finds the trailer at 0.24–0.29, better than COCO. |
| YOLOE-26s, visual prompt from four example sacks | One sack in the pile, never the sack moving on the conveyor. |

Zero-shot detection is not good enough to count; a trained detector is required.

## Steps once footage arrives

1. **Record.** One to two hours from the camera YardLens will analyse (the same stream, or its
   sub-stream if that is what the camera is configured with), covering several trucks, day and
   night, and the sack colours and sizes in use. With FFmpeg:
   `ffmpeg -rtsp_transport tcp -i "rtsp://user:pass@host/..." -c copy -t 01:00:00 bay-1.mp4`
   (VLC's "Convert / Save" works too).
2. **Extract frames.** About 300–500, only while loading is going on:

   ```bash
   cd apps/api
   uv run python scripts/extract_frames.py bay-1.mp4 bay-2.mp4 --every 1 --out data/datasets/bay/images
   ```

   It keeps a frame a second and skips frames that barely changed (a parked truck, an empty bay).
3. **Label** in Label Studio, CVAT or Roboflow, exporting in YOLO format. Two classes, in this
   order:
   - `0 truck`: the vehicle being loaded, truck or trailer. Keep the name `truck`: YardLens treats
     `car`, `truck` and `bus` as vehicles (`LOADING_VEHICLE_CLASSES` in `core/enums.py`).
   - `1 sack`: every sack in view, on the conveyor and in the pile. Leaving sacks in the pile
     unlabelled teaches the model that sacks are background.
4. **Dataset file**, `apps/api/data/datasets/bay/bay.yaml` (`data/` is not in git):

   ```yaml
   path: .            # this folder
   train: images/train
   val: images/val    # 15-20% of the frames, from a different truck than train
   names:
     0: truck
     1: sack
   ```

5. **Train** (20–40 minutes on an RTX 4070):

   ```bash
   cd apps/api
   uv run yolo train data=data/datasets/bay/bay.yaml model=yolo26n.pt epochs=100 imgsz=640 batch=16 device=0
   ```

   Aim for a `sack` mAP50 of 0.8 or more, then watch the predictions on a recording the model has
   not seen (`uv run yolo predict model=runs/detect/train/weights/best.pt source=bay-3.mp4`).
6. **Deploy.** Copy `runs/detect/train/weights/best.pt` to `apps/api/data/models/bay-sacks.pt`.
   Give it to the bay camera only, so the other cameras keep the general model: camera menu →
   **Распознавание** → own settings, model `bay-sacks.pt`, classes `truck` and `sack`, cargo
   `sack`. On a server without an NVIDIA GPU the OpenVINO copy is made on the first load.
7. **Configure the camera.** The loading zone where the truck's wheels stand; the counting line
   across the conveyor where sacks pass just before they drop into the truck; analysis rate 8–15
   fps. If the camera has two streams, the sub-stream as the address and the main stream under
   **Основной поток для номеров**.
8. **Check against a hand count.** Count three or more recorded loadings by eye, then run each
   through the same pipeline, faster than real time, with the camera's line and zone:

   ```bash
   cd apps/api
   uv run yardlens-eval bay-3.mp4 --expected 118 --model bay-sacks.pt --classes truck,sack \
       --cargo sack --line 0.36,0.30,0.46,0.62 --zone "0.2,0.75 1,0.75 1,1 0.2,1"
   ```

   It prints each session and exits with status 1 when the count is off by more than
   `--tolerance`. Off by more than one or two sacks per truck means more labelled frames, usually
   of the cases it missed. Keep these recordings: rerun them after retraining or upgrading.

## Also open before production

- **Linux server.** Never run there yet. Hardware video decoding may not be available in the PyPI
  OpenCV build (it falls back to the CPU); the systemd units in `deploy/linux` are untested;
  OpenVINO on AMD CPUs is untested. CI runs the test suite on Linux, not on real cameras.
- **24-hour run** with real cameras, watching memory and CPU under **Settings → Нагрузка**.
- **One administrator only.** Sign-in, HTTPS and masked camera passwords are done; operators who
  may only watch would need users with roles. See [roadmap.md](roadmap.md) for the other
  architecture work not done yet.

## Demo clips (not in git)

Pexels, free licence, by emirkhan bal. `bun run setup` downloads the first one as
`videos/loading-bay.mp4` and seeds a demo camera with the line and zone below; the others go in
`apps/api/data/videos/<name>.mp4` by hand:

| File | Source (1080p) | Camera settings used |
| --- | --- | --- |
| `bags-to-trailer.mp4` | `https://videos.pexels.com/video-files/11324000/11324000-hd_1920_1080_30fps.mp4` | 10 fps; line (0.36, 0.30)–(0.46, 0.62); zone (0.2, 0.75) (1, 0.75) (1, 1) (0.2, 1) |
| `bags-in-trailer.mp4` | `https://videos.pexels.com/video-files/11326407/11326407-hd_1920_1080_30fps.mp4` | 10 fps; line (0.38, 0.06)–(0.38, 0.42); zone (0.2, 0.66) (0.97, 0.66) (0.97, 0.86) (0.2, 0.86) |
| `bags-on-conveyor.mp4` | `https://videos.pexels.com/video-files/11326416/11326416-hd_1920_1080_30fps.mp4` | Hand-held, not usable as a camera |

They are 13–26 s long: enough to try the pipeline end to end once a model exists, far too little
to train a model that works anywhere else.
