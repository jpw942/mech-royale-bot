"""Offline: per walkable cell, how far you can see on average (16 rays at eye height, capped at 160 m).
Low numbers = tucked-in spots where nobody can snipe you from far away."""
import json, sys, numpy as np
f = open(sys.argv[1], "rb"); hdr = json.loads(f.readline())
nx, ny, C = hdr["nx"], hdr["ny"], hdr["cell"]
walk = np.frombuffer(f.read(nx*ny), np.uint8).reshape(ny, nx)
sol = np.frombuffer(f.read(2*nx*ny), "<i2").reshape(ny, nx) / 10.0
ter = np.frombuffer(f.read(2*nx*ny), "<i2").reshape(ny, nx) / 10.0
eye = ter + 8
J, I = np.mgrid[0:ny, 0:nx]
tot = np.zeros((ny, nx)); far = np.zeros((ny, nx))
MAX = 160
for k in range(16):
    a = 2*np.pi*k/16
    dist = np.full((ny, nx), float(MAX))
    alive = np.ones((ny, nx), bool)
    for s in range(2, MAX, 2):
        ii = np.clip(np.round(I + np.cos(a)*s/C).astype(int), 0, nx-1)
        jj = np.clip(np.round(J + np.sin(a)*s/C).astype(int), 0, ny-1)
        hit = alive & (sol[jj, ii] > eye) 
        dist[hit] = s; alive &= ~hit
    tot += dist; far += dist > 80
exp = tot / 16
out = np.clip(exp / MAX * 255, 0, 255).astype(np.uint8)
out[walk == 0] = 255
out.tofile(sys.argv[2])
print("exposure: median", np.median(exp[walk == 1]), "p10", np.percentile(exp[walk==1], 10))
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
plt.figure(figsize=(14,10)); plt.imshow(np.where(walk==1, exp, np.nan), origin="lower", cmap="magma"); plt.colorbar(); plt.savefig(sys.argv[2]+".png", dpi=70)
