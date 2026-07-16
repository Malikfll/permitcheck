"""Computer-vision analysis of scanned/rasterized drawings (OpenCV).

Pipeline for scanned 2D drawings where no vector geometry survives:

    grayscale -> adaptive binarization -> skew estimation (Hough angle
    histogram) -> probabilistic Hough line detection -> collinear segment
    merging -> metric measurement via the sheet scale (px per drawing unit)
    -> parallel-cluster detection (e.g. stair treads => tread run spacing)

Every measurement carries a confidence derived from detection support
(merged-segment count and straightness residual), feeding the engine's
UNCERTAIN verdict machinery. Scale comes from the title block (known plot
scale + scan DPI) or a detected scale bar; it is a required input here.

Requires numpy + opencv-python. Text on scans still needs OCR (Tesseract
integration is a Phase-2 item); geometry does not.
"""

import math

try:
    import cv2
    import numpy as np
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False

CONFIDENCE_RASTER_BASE = 0.70  # scanned geometry is inherently less certain


# --------------------------------------------------------------------- #
# Rasterization (used by the evaluation harness and for tests)
# --------------------------------------------------------------------- #
def rasterize(segments, circles=(), width_px=2400, pad=40,
              rotate_deg=0.0, noise_sigma=0.0, blur=0, seed=7):
    """Render vector segments to a synthetic 'scan'. Returns (image,
    px_per_unit, transform) where transform maps drawing coords -> pixels."""
    xs = [c for s in segments for c in (s[0][0], s[1][0])]
    ys = [c for s in segments for c in (s[0][1], s[1][1])]
    for c in circles:
        xs += [c[0][0] - c[1], c[0][0] + c[1]]
        ys += [c[0][1] - c[1], c[0][1] + c[1]]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span = max(maxx - minx, 1e-9)
    px_per_unit = (width_px - 2 * pad) / span
    height_px = int((maxy - miny) * px_per_unit) + 2 * pad

    def to_px(p):
        return (int(round(pad + (p[0] - minx) * px_per_unit)),
                int(round(pad + (maxy - p[1]) * px_per_unit)))

    img = np.full((height_px, width_px), 255, np.uint8)
    for s in segments:
        cv2.line(img, to_px(s[0]), to_px(s[1]), 0, 2, cv2.LINE_AA)
    for c in circles:
        cv2.circle(img, to_px(c[0]), int(round(c[1] * px_per_unit)), 0, 2, cv2.LINE_AA)

    if rotate_deg:
        center = (width_px / 2, height_px / 2)
        m = cv2.getRotationMatrix2D(center, rotate_deg, 1.0)
        img = cv2.warpAffine(img, m, (width_px, height_px),
                             flags=cv2.INTER_LINEAR, borderValue=255)
    if noise_sigma:
        rng = np.random.default_rng(seed)
        noisy = img.astype(np.float32) + rng.normal(0, noise_sigma, img.shape)
        img = np.clip(noisy, 0, 255).astype(np.uint8)
    if blur:
        img = cv2.GaussianBlur(img, (blur | 1, blur | 1), 0)
    return img, px_per_unit, to_px


# --------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------- #
def binarize(img):
    return cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                 cv2.THRESH_BINARY_INV, 31, 15)


def estimate_skew_deg(binary):
    """Dominant small rotation of the sheet from the Hough angle histogram."""
    lines = cv2.HoughLines(binary, 1, math.pi / 1440, threshold=200)
    if lines is None:
        return 0.0
    votes = {}
    for rho_theta in lines[:200]:
        theta = rho_theta[0][1]
        deg = math.degrees(theta) - 90.0     # 0 = horizontal line
        for base in (-90.0, 0.0, 90.0):
            d = deg - base
            if abs(d) <= 5.0:
                votes[round(d, 1)] = votes.get(round(d, 1), 0) + 1
    if not votes:
        return 0.0
    return max(votes.items(), key=lambda kv: kv[1])[0]


def detect_segments(binary, min_len_px=25):
    segs = cv2.HoughLinesP(binary, 1, math.pi / 720, threshold=40,
                           minLineLength=min_len_px, maxLineGap=4)
    if segs is None:
        return []
    return [tuple(map(float, s[0])) for s in segs]


def _angle(seg):
    return math.atan2(seg[3] - seg[1], seg[2] - seg[0]) % math.pi


def _proj(seg, axis_cos, axis_sin):
    return (seg[0] * axis_cos + seg[1] * axis_sin,
            seg[2] * axis_cos + seg[3] * axis_sin)


def merge_collinear(segs, angle_tol_deg=2.0, offset_tol_px=3.0, gap_px=8.0):
    """Merge Hough fragments that lie on the same infinite line and (nearly)
    touch, so long drawing lines are measured once at full length."""
    groups = []
    used = [False] * len(segs)
    for i, seg in enumerate(segs):
        if used[i]:
            continue
        cluster = [seg]
        used[i] = True
        a1 = _angle(seg)
        cos_a, sin_a = math.cos(a1), math.sin(a1)
        # signed perpendicular offset of the line through seg
        off1 = -seg[0] * sin_a + seg[1] * cos_a
        for j in range(i + 1, len(segs)):
            if used[j]:
                continue
            a2 = _angle(segs[j])
            diff = min(abs(a1 - a2), math.pi - abs(a1 - a2))
            if math.degrees(diff) > angle_tol_deg:
                continue
            off2 = -segs[j][0] * sin_a + segs[j][1] * cos_a
            if abs(off1 - off2) > offset_tol_px:
                continue
            cluster.append(segs[j])
            used[j] = True
        # merge cluster along the axis, closing small gaps
        intervals = sorted(sorted(_proj(s, cos_a, sin_a)) for s in cluster)
        merged = [list(intervals[0])]
        for lo, hi in intervals[1:]:
            if lo <= merged[-1][1] + gap_px:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        offset = sum((-s[0] * sin_a + s[1] * cos_a) for s in cluster) / len(cluster)
        for lo, hi in merged:
            groups.append({
                "x1": lo * cos_a - offset * sin_a, "y1": lo * sin_a + offset * cos_a,
                "x2": hi * cos_a - offset * sin_a, "y2": hi * sin_a + offset * cos_a,
                "length_px": hi - lo, "angle_deg": math.degrees(a1) % 180.0,
                "support": len(cluster),
            })
    return groups


def _axial_extent(g, cos_a, sin_a):
    a1 = g["x1"] * cos_a + g["y1"] * sin_a
    a2 = g["x2"] * cos_a + g["y2"] * sin_a
    return (min(a1, a2), max(a1, a2))


def detect_parallel_spacing(merged, min_members=3, angle_tol_deg=2.0,
                            len_ratio=0.55, overlap_frac=0.4):
    """Find clusters of >=N parallel, evenly spaced, SPATIALLY CO-LOCATED lines
    (stair treads, joists…) and return the median spacing in pixels.

    Spatial locality matters: a plan is full of parallel walls, so a global
    angle bucket is not enough. Members must (a) be parallel, (b) overlap in
    their extent along the line direction (same corridor), (c) have similar
    length, and (d) form an evenly-spaced run in perpendicular offset."""
    clusters = []
    by_angle = {}
    for g in merged:
        by_angle.setdefault(round(g["angle_deg"] / angle_tol_deg), []).append(g)

    for group in by_angle.values():
        if len(group) < min_members:
            continue
        a = math.radians(group[0]["angle_deg"])
        cos_a, sin_a = math.cos(a), math.sin(a)
        # annotate each line with offset, axial extent, length
        items = []
        for g in group:
            off = -g["x1"] * sin_a + g["y1"] * cos_a
            lo, hi = _axial_extent(g, cos_a, sin_a)
            items.append({"off": off, "lo": lo, "hi": hi,
                          "len": g["length_px"], "g": g})
        items.sort(key=lambda it: it["off"])

        used = [False] * len(items)
        for i in range(len(items)):
            if used[i]:
                continue
            base = items[i]
            corridor = [base]
            for j in range(len(items)):
                if j == i or used[j]:
                    continue
                it = items[j]
                # co-located: axial extents overlap, similar length
                ov = min(base["hi"], it["hi"]) - max(base["lo"], it["lo"])
                span = min(base["hi"] - base["lo"], it["hi"] - it["lo"])
                if span <= 0 or ov < overlap_frac * span:
                    continue
                if min(base["len"], it["len"]) / max(base["len"], it["len"]) < len_ratio:
                    continue
                corridor.append(it)
            if len(corridor) < min_members:
                continue
            offs = sorted(c["off"] for c in corridor)
            # collapse near-duplicate detections (both edges of one stroke)
            dedup = [offs[0]]
            for off in offs[1:]:
                if off - dedup[-1] <= 10.0:
                    dedup[-1] = (dedup[-1] + off) / 2
                else:
                    dedup.append(off)
            if len(dedup) < min_members:
                continue
            gaps = [dedup[k + 1] - dedup[k] for k in range(len(dedup) - 1)]
            gaps_sorted = sorted(gaps)
            median = gaps_sorted[len(gaps_sorted) // 2]
            if median <= 0:
                continue
            spread = (max(gaps) - min(gaps)) / median
            if spread < 0.25:
                for c in corridor:
                    idx = items.index(c)
                    used[idx] = True
                clusters.append({"members": len(dedup), "spacing_px": median,
                                 "angle_deg": group[0]["angle_deg"],
                                 "regularity": round(1 - spread, 3)})
    return clusters


def _ink_continuous(binary, p1, p2, halfwidth=2, min_cover=0.9):
    """Fraction-of-ink check along the straight span p1->p2."""
    h, w = binary.shape
    dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    if dist < 1:
        return True
    steps = max(int(dist), 2)
    hits = 0
    nx, ny = -(p2[1] - p1[1]) / dist, (p2[0] - p1[0]) / dist
    for k in range(steps + 1):
        t = k / steps
        x, y = p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1])
        found = False
        for o in range(-halfwidth, halfwidth + 1):
            xi, yi = int(round(x + o * nx)), int(round(y + o * ny))
            if 0 <= xi < w and 0 <= yi < h and binary[yi, xi] > 0:
                found = True
                break
        if found:
            hits += 1
    return hits / (steps + 1) >= min_cover


def rejoin_fragments(groups, binary, angle_tol_deg=0.8, min_len_px=100,
                     max_gap_px=450.0):
    """Second-stage merge: Hough angle quantization splits very long lines
    into groups the strict first stage cannot join. Rejoin two long groups
    when they are nearly parallel, collinear (origin-independent midpoint
    test), axially consecutive, and the connecting span is continuous ink."""
    groups = list(groups)
    merged_any = True
    while merged_any:
        merged_any = False
        for i in range(len(groups)):
            gi = groups[i]
            if gi is None or gi["length_px"] < min_len_px:
                continue
            for j in range(len(groups)):
                gj = groups[j]
                if i == j or gj is None or gj["length_px"] < min_len_px:
                    continue
                d_ang = abs(gi["angle_deg"] - gj["angle_deg"]) % 180.0
                d_ang = min(d_ang, 180.0 - d_ang)
                if d_ang > angle_tol_deg:
                    continue
                a = math.radians(gi["angle_deg"])
                cos_a, sin_a = math.cos(a), math.sin(a)
                mi = ((gi["x1"] + gi["x2"]) / 2, (gi["y1"] + gi["y2"]) / 2)
                mj = ((gj["x1"] + gj["x2"]) / 2, (gj["y1"] + gj["y2"]) / 2)
                dxm, dym = mj[0] - mi[0], mj[1] - mi[1]
                axial = abs(dxm * cos_a + dym * sin_a)
                if abs(-dxm * sin_a + dym * cos_a) > 3.0 + 0.004 * axial:
                    continue
                # axial ordering and gap
                pi_ = sorted((gi["x1"] * cos_a + gi["y1"] * sin_a,
                              gi["x2"] * cos_a + gi["y2"] * sin_a))
                pj_ = sorted((gj["x1"] * cos_a + gj["y1"] * sin_a,
                              gj["x2"] * cos_a + gj["y2"] * sin_a))
                gap = max(pi_[0], pj_[0]) - min(pi_[1], pj_[1])
                if gap > max_gap_px:
                    continue
                # connecting span must be real ink
                if gap > 2:
                    if pi_[1] < pj_[0]:
                        pa = (gi["x2"], gi["y2"]) if (gi["x2"] * cos_a + gi["y2"] * sin_a) == pi_[1] else (gi["x1"], gi["y1"])
                        pb = (gj["x1"], gj["y1"]) if (gj["x1"] * cos_a + gj["y1"] * sin_a) == pj_[0] else (gj["x2"], gj["y2"])
                    else:
                        pa = (gj["x2"], gj["y2"]) if (gj["x2"] * cos_a + gj["y2"] * sin_a) == pj_[1] else (gj["x1"], gj["y1"])
                        pb = (gi["x1"], gi["y1"]) if (gi["x1"] * cos_a + gi["y1"] * sin_a) == pi_[0] else (gi["x2"], gi["y2"])
                    if not _ink_continuous(binary, pa, pb):
                        continue
                # merge into gi
                off = (-mi[0] * sin_a + mi[1] * cos_a + -mj[0] * sin_a + mj[1] * cos_a) / 2
                lo, hi = min(pi_[0], pj_[0]), max(pi_[1], pj_[1])
                gi.update(x1=lo * cos_a - off * sin_a, y1=lo * sin_a + off * cos_a,
                          x2=hi * cos_a - off * sin_a, y2=hi * sin_a + off * cos_a,
                          length_px=hi - lo,
                          support=gi["support"] + gj["support"])
                groups[j] = None
                merged_any = True
    return [g for g in groups if g is not None]


def refit_line(binary, g, stations=41, search_px=5):
    """Correct a long group's angle/offset by least-squares fitting the ink
    centroids sampled along it - Hough angle quantization (~0.25 deg) makes
    the nominal line drift off the ink over long distances."""
    a = math.radians(g["angle_deg"])
    cos_a, sin_a = math.cos(a), math.sin(a)
    h, w = binary.shape
    pts = []
    for i in range(stations):
        t = g["length_px"] * i / (stations - 1)
        cx = g["x1"] + t * cos_a
        cy = g["y1"] + t * sin_a
        num = den = 0.0
        for o_tenths in range(-search_px * 10, search_px * 10 + 1, 5):
            o = o_tenths / 10.0
            xi = int(round(cx - o * sin_a))
            yi = int(round(cy + o * cos_a))
            if 0 <= xi < w and 0 <= yi < h and binary[yi, xi] > 0:
                num += o
                den += 1
        if den:
            o_c = num / den
            pts.append((t, o_c))
    if len(pts) < max(5, stations // 3):
        return g
    # least squares o = m*t + b in the (axial, perpendicular) frame
    n = len(pts)
    st = sum(p[0] for p in pts)
    so = sum(p[1] for p in pts)
    stt = sum(p[0] * p[0] for p in pts)
    sto = sum(p[0] * p[1] for p in pts)
    denom = n * stt - st * st
    if abs(denom) < 1e-9:
        return g
    m = (n * sto - st * so) / denom
    b = (so * stt - st * sto) / denom
    # corrected endpoints in image space
    def corrected(t):
        o = m * t + b
        return (g["x1"] + t * cos_a - o * sin_a, g["y1"] + t * sin_a + o * cos_a)
    (nx1, ny1), (nx2, ny2) = corrected(0.0), corrected(g["length_px"])
    out = dict(g)
    out.update(x1=nx1, y1=ny1, x2=nx2, y2=ny2,
               length_px=math.hypot(nx2 - nx1, ny2 - ny1),
               angle_deg=math.degrees(math.atan2(ny2 - ny1, nx2 - nx1)) % 180.0)
    return out


def refine_extent(binary, g, extend_px=None, halfwidth=2, gap_px=5.0,
                  junctions=(None, None)):
    """Refine a merged segment's endpoints by measuring the actual ink extent
    along its axis in the binary image (sub-pixel-ish, robust to Hough
    endpoint jitter). Returns the refined length in px, cap-compensated."""
    h, w = binary.shape
    a = math.radians(g["angle_deg"])
    cos_a, sin_a = math.cos(a), math.sin(a)
    x0, y0 = g["x1"], g["y1"]
    length = g["length_px"]
    if extend_px is None:
        # short segments only need local endpoint-jitter recovery; long lines
        # may continue through dense regions where Hough sees no fragments at
        # all, so the ink walk gets a proportionally longer leash
        extend_px = min(max(12.0, 0.35 * length), 600.0)

    def ink(t, o):
        x = x0 + t * cos_a - o * sin_a
        y = y0 + t * sin_a + o * cos_a
        xi, yi = int(round(x)), int(round(y))
        return 0 <= xi < w and 0 <= yi < h and binary[yi, xi] > 0

    step = 0.5
    t = -extend_px
    runs, run_start, last_ink = [], None, None
    while t <= length + extend_px:
        has = any(ink(t, o) for o in range(-halfwidth, halfwidth + 1))
        if has and run_start is None:
            run_start = t
        elif not has and run_start is not None:
            if last_ink is not None and runs and run_start - runs[-1][1] <= gap_px:
                runs[-1][1] = t - step          # bridge small gap
            else:
                runs.append([run_start, t - step])
            run_start = None
        if has:
            last_ink = t
        t += step
    if run_start is not None:
        runs.append([run_start, length + extend_px])
    if not runs:
        return length
    # pick the run overlapping the detected segment the most; where the end
    # meets a crossing line (corner or T-junction) snap it to the exact
    # vector intersection; free ends get a half-stroke cap compensation
    best = max(runs, key=lambda r: min(r[1], length) - max(r[0], 0.0))
    stroke = _stroke_width(binary, g)
    lo, hi = best
    t_lo, t_hi = junctions
    if t_lo is not None and abs(lo - t_lo) <= stroke + 2.5:
        lo = t_lo
    else:
        lo += stroke / 2.0
    if t_hi is not None and abs(hi - t_hi) <= stroke + 2.5:
        hi = t_hi
    else:
        hi -= stroke / 2.0
    return max(hi - lo, 1.0)


def _stroke_width(binary, g, samples=9):
    """Median ink thickness perpendicular to the segment (for cap compensation)."""
    h, w = binary.shape
    a = math.radians(g["angle_deg"])
    cos_a, sin_a = math.cos(a), math.sin(a)
    widths = []
    for i in range(1, samples + 1):
        t = g["length_px"] * i / (samples + 1)
        cnt = 0
        for o_tenths in range(-60, 61):
            o = o_tenths / 10.0
            x = g["x1"] + t * cos_a - o * sin_a
            y = g["y1"] + t * sin_a + o * cos_a
            xi, yi = int(round(x)), int(round(y))
            if 0 <= xi < w and 0 <= yi < h and binary[yi, xi] > 0:
                cnt += 1
        widths.append(cnt / 10.0)
    widths.sort()
    return widths[len(widths) // 2]


# --------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------- #
def analyze(image_or_path, px_per_unit, unit="mm", min_len_px=25):
    """Analyze a scanned drawing. Returns measured line lengths and parallel
    spacing clusters in drawing units, with per-measurement confidence."""
    if not HAVE_CV2:
        raise RuntimeError("opencv-python + numpy are required: pip install opencv-python-headless numpy")
    img = image_or_path
    if isinstance(image_or_path, str):
        img = cv2.imread(image_or_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError("cannot read image: %s" % image_or_path)
    binary = binarize(img)
    skew = estimate_skew_deg(binary)
    merged = merge_collinear(detect_segments(binary, min_len_px))
    merged = sorted(rejoin_fragments(merged, binary), key=lambda g: -g["length_px"])

    def junction_t(idx, px_pt, t_ref):
        """If another, non-parallel detected line passes near this endpoint,
        return the axial coordinate (in g's t-space) of the exact vector
        intersection - the true corner/T-junction position. Else None."""
        g = merged[idx]
        a = math.radians(g["angle_deg"])
        ca, sa = math.cos(a), math.sin(a)
        best = None
        for k, h in enumerate(merged):
            if k == idx:
                continue
            d_ang = abs(g["angle_deg"] - h["angle_deg"]) % 180.0
            if min(d_ang, 180.0 - d_ang) < 20.0:
                continue
            vx, vy = h["x2"] - h["x1"], h["y2"] - h["y1"]
            ll = vx * vx + vy * vy
            if ll == 0:
                continue
            u = max(0.0, min(1.0, ((px_pt[0] - h["x1"]) * vx + (px_pt[1] - h["y1"]) * vy) / ll))
            dx, dy = px_pt[0] - (h["x1"] + u * vx), px_pt[1] - (h["y1"] + u * vy)
            if math.hypot(dx, dy) > 6.0:
                continue
            b = math.radians(h["angle_deg"])
            denom = ca * math.sin(b) - sa * math.cos(b)
            if abs(denom) < 1e-9:
                continue
            wx, wy = h["x1"] - g["x1"], h["y1"] - g["y1"]
            t = (wx * math.sin(b) - wy * math.cos(b)) / denom
            if best is None or abs(t - t_ref) < abs(best - t_ref):
                best = t
        return best

    # long lines: correct Hough angle drift against the actual ink before
    # walking their extent
    merged = [refit_line(binary, g) if g["length_px"] > 350 else g for g in merged]

    measurements = []
    for idx, g in enumerate(merged):
        junctions = (junction_t(idx, (g["x1"], g["y1"]), 0.0),
                     junction_t(idx, (g["x2"], g["y2"]), g["length_px"]))
        refined_px = refine_extent(binary, g, junctions=junctions)
        conf = CONFIDENCE_RASTER_BASE + min(0.2, 0.04 * (g["support"] - 1))
        measurements.append({
            "kind": "line_length", "value": round(refined_px / px_per_unit, 3),
            "unit": unit, "angle_deg": round(g["angle_deg"], 2),
            "length_px_refined": round(refined_px, 2),
            "confidence": round(conf, 2),
            "px": {k: round(g[k], 1) for k in ("x1", "y1", "x2", "y2")},
        })
    spacing = [{
        "kind": "parallel_spacing", "value": round(c["spacing_px"] / px_per_unit, 3),
        "unit": unit, "members": c["members"], "regularity": c["regularity"],
        "confidence": round(min(0.9, CONFIDENCE_RASTER_BASE + 0.05 * c["members"]
                                * c["regularity"]), 2),
    } for c in detect_parallel_spacing(merged)]
    return {"skew_deg": skew, "n_raw_segments": len(merged),
            "measurements": measurements, "parallel_clusters": spacing}
