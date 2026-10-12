"""Coordinate conversion only; snapshots, consent and target identity stay in Desktop."""
import math


def numbers(value, length, name):
    if (not isinstance(value, (list, tuple)) or len(value) != length or
            any(isinstance(v, bool) or not isinstance(v, (int, float)) or
                not math.isfinite(v) for v in value)):
        raise ValueError(f'{name} requires {length} finite numbers')
    return list(value)


def size(value, name):
    out = numbers(value, 2, name)
    if min(out) <= 0:
        raise ValueError(f'{name} must be positive')
    return out


def rect(value, name):
    out = numbers(value, 4, name)
    size(out[2:], name)
    return out


def center(bounds):
    x, y, w, h = rect(bounds, 'bounds')
    return x + w / 2, y + h / 2


def normalized(point, dimensions):
    x, y = numbers(point, 2, 'point')
    w, h = size(dimensions, 'dimensions')
    if not (0 <= x < w and 0 <= y < h):
        raise ValueError('Point is outside the selected image/monitor; no clamping')
    return {'x': x / w, 'y': y / h}


def image_point(point, image):
    return normalized(point, [image.get('width'), image.get('height')])


def logical_point(point, image):
    # Position is optional in the portal protocol. Missing does not mean (0, 0).
    ox, oy = numbers(image.get('logical_position'), 2, 'monitor logical_position')
    x, y = numbers(point, 2, 'point')
    return normalized([x - ox, y - oy], image.get('logical_size'))


def relative_box(bounds, source, destination):
    """Map a rectangle through two measured frames; never infer browser chrome size."""
    x, y, w, h = rect(bounds, 'bounds')
    sx, sy, sw, sh = rect(source, 'source frame')
    dx, dy, dw, dh = rect(destination, 'destination frame')
    if x < sx or y < sy or x + w > sx + sw or y + h > sy + sh:
        raise ValueError('Target is not fully inside its measured frame')
    rx, ry = dw / sw, dh / sh
    # A window/document with inconsistent extents is not a calibrated viewport.
    if not math.isclose(rx, ry, rel_tol=.005, abs_tol=1e-9):
        raise ValueError('Measured frames have incompatible aspect ratios; re-observe')
    return [dx + (x - sx) * rx, dy + (y - sy) * ry, w * rx, h * ry]


def portal_point(x, y, logical_size):
    # Preserve the public legacy normalized API's inclusive 1.0 endpoint.
    numbers([x, y], 2, 'normalized point')
    if not 0 <= x <= 1 or not 0 <= y <= 1:
        raise ValueError('Normalized point must be within 0..1')
    w, h = size(logical_size, 'logical_size')
    return min(x * w, w - 1), min(y * h, h - 1)


def pointer_calibration(samples):
    """Fit CSS -> compositor from three physical moves, with an independent third check.

    Each sample pairs the actual portal destination with the page's trusted clientX/Y.
    No devicePixelRatio, window decoration estimate or browser screenX is involved.
    """
    if not isinstance(samples, list) or len(samples) != 3:
        raise ValueError('Calibration requires three pointer samples')
    pairs = [(numbers(s['client'], 2, 'client point'),
              numbers(s['logical'], 2, 'logical point')) for s in samples]
    scale, offset = [], []
    for axis in (0, 1):
        delta = pairs[1][0][axis] - pairs[0][0][axis]
        if abs(delta) < 30:
            raise ValueError('Calibration samples are too close')
        ratio = (pairs[1][1][axis] - pairs[0][1][axis]) / delta
        if not 0.1 <= ratio <= 10:
            raise ValueError('Invalid measured pointer scale')
        origin = pairs[0][1][axis] - ratio * pairs[0][0][axis]
        if abs(origin + ratio * pairs[2][0][axis] - pairs[2][1][axis]) > 2:
            raise ValueError('Independent pointer sample disagrees; no click was sent')
        scale.append(ratio)
        offset.append(origin)
    if not math.isclose(*scale, rel_tol=.02):
        raise ValueError('Inconsistent measured pointer axes')
    return {'scale': scale, 'origin': offset, 'samples': samples}


def calibrated_point(point, calibration):
    xy = numbers(point, 2, 'CSS point')
    scale = size(calibration['scale'], 'measured scale')
    origin = numbers(calibration['origin'], 2, 'measured origin')
    return [origin[i] + xy[i] * scale[i] for i in (0, 1)]
