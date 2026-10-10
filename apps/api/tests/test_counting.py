import numpy as np
import pytest
import supervision as sv

from tests.conftest import make_detections
from yardlens_api.schemas.camera import CountingLine
from yardlens_api.services.analyzer import FrameAnalyzer, LineCounter, line_to_pixels

WIDTH, HEIGHT = 640, 480
HORIZONTAL = CountingLine(x1=0.0, y1=0.5, x2=1.0, y2=0.5)
FRAME_INTERVAL = 0.2  # 5 fps inference


def _frame() -> np.ndarray[tuple[int, int, int], np.dtype[np.uint8]]:
    return np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)


def _box(
    center_y: float, center_x: float = 320.0, size: float = 100.0
) -> tuple[float, float, float, float]:
    half = size / 2
    return (center_x - half, center_y - half, center_x + half, center_y + half)


def _path(start: float, stop: float, step: float) -> list[float]:
    return [float(value) for value in np.arange(start, stop, step)]


def _run(
    analyzer: FrameAnalyzer, centers: list[float], class_name: str = "car"
) -> list[tuple[int, int, int]]:
    """Feed a single object along ``centers``; return (entered, in, out) per frame."""
    history: list[tuple[int, int, int]] = []
    for index, center in enumerate(centers):
        observation = analyzer.process(
            _frame(), make_detections([_box(center)], [class_name]), index * FRAME_INTERVAL
        )
        history.append(
            (len(observation.entered), len(observation.crossed_in), len(observation.crossed_out))
        )
    return history


def test_line_to_pixels_scales_normalized_points() -> None:
    start, end = line_to_pixels(CountingLine(x1=0.25, y1=0.5, x2=0.75, y2=1.0), WIDTH, HEIGHT)
    assert (start.x, start.y, end.x, end.y) == (160.0, 240.0, 480.0, 480.0)


def test_counting_line_rejects_identical_points() -> None:
    with pytest.raises(ValueError, match="distinct"):
        CountingLine(x1=0.5, y1=0.5, x2=0.5, y2=0.5)


def test_moving_up_across_left_to_right_line_counts_in() -> None:
    analyzer = FrameAnalyzer(fps=5, line=HORIZONTAL)
    history = _run(analyzer, _path(420, 40, -20))
    assert sum(crossed_in for _, crossed_in, _ in history) == 1
    assert sum(crossed_out for _, _, crossed_out in history) == 0
    assert (analyzer.line_in, analyzer.line_out) == (1, 0)


def test_moving_down_counts_out() -> None:
    analyzer = FrameAnalyzer(fps=5, line=HORIZONTAL)
    _run(analyzer, _path(40, 440, 20))
    assert (analyzer.line_in, analyzer.line_out) == (0, 1)


def test_reversed_line_swaps_direction() -> None:
    analyzer = FrameAnalyzer(fps=5, line=CountingLine(x1=1.0, y1=0.5, x2=0.0, y2=0.5))
    _run(analyzer, _path(420, 40, -20))
    assert (analyzer.line_in, analyzer.line_out) == (0, 1)


def test_object_staying_on_one_side_is_not_counted() -> None:
    analyzer = FrameAnalyzer(fps=5, line=HORIZONTAL)
    _run(analyzer, [400.0] * 10)
    assert (analyzer.line_in, analyzer.line_out) == (0, 0)


def test_object_entered_is_emitted_once_per_track() -> None:
    analyzer = FrameAnalyzer(fps=5, line=None, min_hits=2)
    history = _run(analyzer, [200.0] * 8, class_name="truck")
    assert sum(entered for entered, _, _ in history) == 1
    observation = analyzer.process(_frame(), make_detections([_box(200.0)], ["truck"]), 10.0)
    assert observation.visible == {"truck": 1}
    assert observation.entered == []


def test_single_frame_flicker_is_not_announced() -> None:
    analyzer = FrameAnalyzer(fps=5, line=None, min_hits=3)
    history = _run(analyzer, [200.0, 200.0])
    empty = analyzer.process(_frame(), sv.Detections.empty(), 10.0)
    assert sum(entered for entered, _, _ in history) == 0
    assert empty.visible == {}


def test_changing_line_keeps_cumulative_counts() -> None:
    analyzer = FrameAnalyzer(fps=5, line=HORIZONTAL)
    _run(analyzer, _path(420, 40, -20))
    analyzer.set_line(CountingLine(x1=0.0, y1=0.3, x2=1.0, y2=0.3))
    assert analyzer.line_in == 1
    analyzer.set_line(None)
    assert analyzer.line is None
    assert analyzer.line_in == 1


def test_line_counter_tracks_totals_per_resolution() -> None:
    counter = LineCounter(HORIZONTAL)
    zone = counter.zone_for(WIDTH, HEIGHT)
    assert counter.zone_for(WIDTH, HEIGHT) is zone
    assert counter.zone_for(1280, 720) is not zone


def test_render_draws_on_a_copy_with_the_same_shape() -> None:
    analyzer = FrameAnalyzer(fps=5, line=HORIZONTAL)
    _run(analyzer, [200.0, 200.0])
    frame = _frame()
    rendered = analyzer.render(frame, 0.3)
    assert rendered.shape == (HEIGHT, WIDTH, 3)
    assert rendered.any()
    assert not frame.any()


def test_predicted_boxes_follow_object_motion_between_inferences() -> None:
    analyzer = FrameAnalyzer(fps=5, line=None)
    # The object moves up by 20 px every 0.2 s, i.e. 100 px/s.
    _run(analyzer, _path(400, 300, -20))
    last_time = 4 * FRAME_INTERVAL
    (at_inference,) = analyzer.predicted(last_time)
    (later,) = analyzer.predicted(last_time + 0.1)
    assert at_inference.box[1] == pytest.approx(_box(320.0)[1], abs=1.0)
    assert later.box[1] == pytest.approx(at_inference.box[1] - 10.0, abs=3.0)


def test_prediction_is_capped_for_stale_inference() -> None:
    analyzer = FrameAnalyzer(fps=5, line=None)
    _run(analyzer, _path(400, 300, -20))
    last_time = 4 * FRAME_INTERVAL
    (capped,) = analyzer.predicted(last_time + 30.0)
    (limit,) = analyzer.predicted(last_time + 0.3)
    assert capped.box == limit.box
