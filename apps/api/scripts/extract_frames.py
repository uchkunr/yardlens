"""Pull training frames out of recorded loading-bay footage.

    uv run python scripts/extract_frames.py bay-1.mp4 bay-2.mp4 --out data/datasets/bay/images

Keeps a frame every ``--every`` seconds and skips frames that barely differ from the last one kept
(a parked truck with nothing moving, an empty bay), so labelling time goes into frames that teach
the model something. See docs/sack-model.md.
"""

import argparse
from pathlib import Path

import cv2
import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract training frames from bay recordings.")
    parser.add_argument("videos", type=Path, nargs="+")
    parser.add_argument("--every", type=float, default=1.0, help="seconds between frames")
    parser.add_argument("--out", type=Path, default=Path("data/datasets/bay/images"))
    parser.add_argument(
        "--min-change",
        type=float,
        default=2.0,
        help="mean grey-level difference (0-255) from the last kept frame needed to keep another",
    )
    parser.add_argument("--max-width", type=int, default=1920)
    args = parser.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    total = 0
    for video in args.videos:
        capture = cv2.VideoCapture(str(video))
        if not capture.isOpened():
            parser.error(f"cannot open {video}")
        fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        step = max(1, round(fps * args.every))
        last: np.ndarray | None = None
        index = kept = 0
        while capture.grab():
            if index % step == 0:
                ok, frame = capture.retrieve()
                if ok:
                    if frame.shape[1] > args.max_width:
                        height = round(frame.shape[0] * args.max_width / frame.shape[1])
                        frame = cv2.resize(
                            frame, (args.max_width, height), interpolation=cv2.INTER_AREA
                        )
                    thumb = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 90))
                    if last is None or float(np.mean(cv2.absdiff(thumb, last))) >= args.min_change:
                        name = out / f"{video.stem}-{index:07d}.jpg"
                        cv2.imwrite(str(name), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                        last = thumb
                        kept += 1
            index += 1
        capture.release()
        print(f"{video.name}: kept {kept} of {index // step + 1} sampled frames")
        total += kept
    print(f"{total} frames in {out}")


if __name__ == "__main__":
    main()
