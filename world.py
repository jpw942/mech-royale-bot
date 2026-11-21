"""Static map model: walkable grid, solid heights for line-of-sight, time-sliced A*."""
import heapq
import json
import math
import os
import time
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
SQ2 = math.sqrt(2)


class World:
    def __init__(self, path=os.path.join(HERE, "grid.bin")):
        with open(path, "rb") as f:
            hdr = json.loads(f.readline())
            self.x0, self.y0, self.c = hdr["x0"], hdr["y0"], hdr["cell"]
            self.nx, self.ny = hdr["nx"], hdr["ny"]
            n = self.nx * self.ny
            self.walk = bytearray(f.read(n))
            self.solid = array("h")
            self.solid.frombytes(f.read(2 * n))
            self.ter = array("h")
            self.ter.frombytes(f.read(2 * n))
        try:
            with open(os.path.join(os.path.dirname(path), "exposure.bin"), "rb") as f:
                self.expo = bytearray(f.read())
        except OSError:
            self.expo = bytearray([128]) * (self.nx * self.ny)

    # --- cells ---
    def cell(self, x, y):
        i = int((x - self.x0) / self.c)
        j = int((y - self.y0) / self.c)
        if i < 0: i = 0
        elif i >= self.nx: i = self.nx - 1
        if j < 0: j = 0
        elif j >= self.ny: j = self.ny - 1
        return i, j

    def center(self, i, j):
        return (self.x0 + (i + 0.5) * self.c, self.y0 + (j + 0.5) * self.c)

    def walkable(self, x, y):
        i, j = self.cell(x, y)
        return self.walk[j * self.nx + i] == 1

    def exposure(self, x, y):
        i, j = self.cell(x, y)
        return self.expo[j * self.nx + i] * (160 / 255)

    def ground(self, x, y):
        i, j = self.cell(x, y)
        return self.ter[j * self.nx + i] * 0.1

    def solid_at(self, x, y):
        i, j = self.cell(x, y)
        return self.solid[j * self.nx + i] * 0.1

    def nearest_walkable(self, x, y, max_r=40):
        i0, j0 = self.cell(x, y)
        nx, walk = self.nx, self.walk
        if walk[j0 * nx + i0]:
            return i0, j0
        for r in range(1, max_r):
            best = None
            for di in range(-r, r + 1):
                for dj in (-r, r) if abs(di) != r else range(-r, r + 1):
                    i, j = i0 + di, j0 + dj
                    if 0 <= i < nx and 0 <= j < self.ny and walk[j * nx + i]:
                        d = di * di + dj * dj
                        if best is None or d < best[0]:
                            best = (d, i, j)
            if best:
                return best[1], best[2]
        return i0, j0

    # --- lines ---
    def los(self, a, b, skip_a=3.0, skip_b=2.0, step=1.5):
        """True if the straight segment a->b (3D) clears ground-rooted solids."""
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        L = math.sqrt(dx * dx + dy * dy)
        if L < 1e-6:
            return True
        n = int(L / step)
        x0, y0, c, nx, ny, solid = self.x0, self.y0, self.c, self.nx, self.ny, self.solid
        for k in range(1, n):
            s = k * step
            if s < skip_a or s > L - skip_b:
                continue
            f = s / L
            x = a[0] + dx * f; y = a[1] + dy * f
            i = int((x - x0) / c); j = int((y - y0) / c)
            if i < 0 or j < 0 or i >= nx or j >= ny:
                return False
            if solid[j * nx + i] * 0.1 > a[2] + dz * f:
                return False
        return True

    def clear_walk(self, a, b):
        """Cells along the 2D segment a->b are all walkable."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        n = int(L / (self.c * 0.5)) + 1
        walk, nx = self.walk, self.nx
        for k in range(n + 1):
            f = k / n
            i, j = self.cell(a[0] + dx * f, a[1] + dy * f)
            if not walk[j * nx + i]:
                return False
        return True


class Planner:
    """A* on the walk grid, run a few ms per tick so we never blow the 50 ms budget."""

    def __init__(self, world):
        self.w = world
        self.job = None
        self.path = None        # list of (x, y) waypoints
        self.goal = None
        self.age = 0.0

    def request(self, start, goal, t):
        self.goal = goal
        self.job = self._astar(start, goal)
        self.req_t = t

    def step(self, budget_s):
        if self.job is None:
            return
        end = time.perf_counter() + budget_s
        try:
            while time.perf_counter() < end:
                for _ in range(4):           # _astar yields every 50 expansions: check the clock every ~200
                    r = next(self.job)
                    if r is not None:
                        self.path = r
                        self.job = None
                        return
        except StopIteration:
            self.job = None

    def _astar(self, start, goal):
        w = self.w
        nx, walk = w.nx, w.walk
        si, sj = w.nearest_walkable(*start)
        gi, gj = w.nearest_walkable(*goal)
        s, g = sj * nx + si, gj * nx + gi
        H = 1.3
        def h(n):
            di = abs(n % nx - gi); dj = abs(n // nx - gj)
            return H * (max(di, dj) + (SQ2 - 1) * min(di, dj))
        openq = [(h(s), 0.0, s)]
        gs = {s: 0.0}
        came = {s: -1}
        nbr = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
               (1, 1, SQ2), (1, -1, SQ2), (-1, 1, SQ2), (-1, -1, SQ2)]
        closed = set()
        it = 0
        while openq:
            f, gc, n = heapq.heappop(openq)
            if n in closed:
                continue
            if n == g:
                break
            closed.add(n)
            i, j = n % nx, n // nx
            for di, dj, cost in nbr:
                ii, jj = i + di, j + dj
                if ii < 0 or jj < 0 or ii >= nx or jj >= w.ny:
                    continue
                m = jj * nx + ii
                if not walk[m]:
                    continue
                if di and dj and not (walk[j * nx + ii] and walk[jj * nx + i]):
                    continue
                ng = gc + cost
                if ng < gs.get(m, 1e18):
                    gs[m] = ng
                    came[m] = n
                    heapq.heappush(openq, (ng + h(m), ng, m))
            it += 1
            if it % 50 == 0:
                yield None
        if g not in came:
            yield [goal]
            return
        cells = []
        n = g
        while n != -1:
            cells.append(n)
            n = came[n]
        cells.reverse()
        pts = [w.center(n % nx, n // nx) for n in cells[::2]]
        pts.append(w.center(gi, gj))
        yield pts
