"""Offline: turn arena/v2 into compact grids the bot loads at startup (stdlib only at runtime).
Outputs grid.bin: header json line + walk mask (uint8, 2 m cells) + solid height (int16 dm) + terrain (int16 dm)."""
import json, sys, numpy as np
from matplotlib.path import Path
from scipy import ndimage

arena = sys.argv[1]; out = sys.argv[2]
w = json.load(open(f"{arena}/v2/world.json"))
T = w["terrain"]
ter = np.fromfile(f"{arena}/v2/terrain.i16", dtype="<i2").astype(np.float32).reshape(T["ny"], T["nx"]) * T["scale"]
ny, nx = ter.shape; x0, y0 = T["minX"], T["minY"]
xs = x0 + np.arange(nx); ys = y0 + np.arange(ny)
X, Y = np.meshgrid(xs, ys)
pts = np.column_stack([X.ravel(), Y.ravel()])

wall = np.zeros((ny, nx), bool)      # blocks a walking mech
solid = ter.copy()                     # top of ground-rooted solids (for LOS / cover)
for p in w["prisms"]:
    r = np.array(p["ring"]).reshape(-1, 2)
    mnx, mny = r.min(0); mxx, mxy = r.max(0)
    i0 = max(int(mnx - x0) - 1, 0); i1 = min(int(mxx - x0) + 2, nx)
    j0 = max(int(mny - y0) - 1, 0); j1 = min(int(mxy - y0) + 2, ny)
    if i1 <= i0 or j1 <= j0: continue
    sub = np.column_stack([X[j0:j1, i0:i1].ravel(), Y[j0:j1, i0:i1].ravel()])
    m = Path(r).contains_points(sub)
    for h in p["holes"]:
        hr = np.array(h).reshape(-1, 2)
        m &= ~Path(hr).contains_points(sub)
    m = m.reshape(j1 - j0, i1 - i0)
    g = ter[j0:j1, i0:i1]
    blocks = m & (p["top"] > g + 1.2) & (p["base"] < g + 9)
    wall[j0:j1, i0:i1] |= blocks
    rooted = m & (p["base"] < g + 4)
    s = solid[j0:j1, i0:i1]
    solid[j0:j1, i0:i1] = np.where(rooted, np.maximum(s, p["top"]), s)

gy, gx = np.gradient(ter)
slope = np.degrees(np.arctan(np.hypot(gx, gy)))
wall |= slope > 45
# map edge margin
wall[:4, :] = True; wall[-4:, :] = True; wall[:, :4] = True; wall[:, -4:] = True

yy, xx = np.mgrid[-3:4, -3:4]
disk = (xx**2 + yy**2) <= 9.5
inflated = ndimage.binary_dilation(wall, structure=disk)

C = 2
gny, gnx = ny // C, nx // C
def pool(a, f):
    a = a[:gny*C, :gnx*C].reshape(gny, C, gnx, C)
    return f(f(a, axis=3), axis=1)
walk = ~pool(inflated, np.any)
sol = pool(solid, np.max)
tr = pool(ter, np.mean)
# keep only the biggest connected walkable region
lab, n = ndimage.label(walk)
sizes = ndimage.sum(walk, lab, range(1, n + 1))
walk = lab == (1 + int(np.argmax(sizes)))
print("cells", gnx, gny, "walkable", walk.mean(), "regions", n)
hdr = {"x0": x0, "y0": y0, "cell": C, "nx": gnx, "ny": gny}
with open(out, "wb") as f:
    f.write((json.dumps(hdr) + "\n").encode())
    f.write(walk.astype(np.uint8).tobytes())
    f.write(np.round(sol * 10).astype("<i2").tobytes())
    f.write(np.round(tr * 10).astype("<i2").tobytes())
# preview
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(14, 10))
ax.imshow(np.where(walk, sol - tr, np.nan), origin="lower", extent=[x0, x0+gnx*C, y0, y0+gny*C], cmap="viridis")
ax.imshow(np.where(walk, np.nan, 1), origin="lower", extent=[x0, x0+gnx*C, y0, y0+gny*C], cmap="Reds", alpha=0.6, vmin=0, vmax=1)
plt.savefig(out + ".png", dpi=80)
