"""Cockroach: a survival-first Mech Royale bot.
Stay in the zone, fight from the front, break line of sight to regen, farm cubes when it's quiet."""
import math
import random
import time

import mechfight as mf
from world import World, Planner

TAU = 2 * math.pi
WEAPON_RANK = {"lightning": 3.0, "laser": 2.5, "mortar": 1.0, "gun": 0.0}
UPGRADE_PREF = ["armor", "plating", "nanites", "gun_rate", "gun_damage", "lightning_efficiency",
                "laser_damage", "laser_charge", "gyros", "servos", "lightning_range", "capacitor",
                "reactor", "gun_mag", "sensors", "lightning_chain", "gun_velocity", "laser_pierce",
                "jets", "mortar_reload", "mortar_velocity", "mortar_splash"]


def wrap(a):
    return (a + math.pi) % TAU - math.pi


def norm2(x, y):
    d = math.hypot(x, y)
    return (x / d, y / d) if d > 1e-9 else (0.0, 0.0)


def d2(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


class Bot:
    name = "cockroach"

    def __init__(self, hello):
        t0 = time.perf_counter()
        self.cfg = hello["config"]
        self.me_id = hello["slot"]
        self.rng = random.Random(hello.get("seed", 0))
        self.w = World()
        self.plan = Planner(self.w)
        self.plan_goal = None
        self.plan_t = -99
        self.enemies = {}          # id -> dict(pos, vel, t, hp, max_hp, weapons, kind)
        self.threats = []          # (t, bearing, weight)
        self.last_hit_t = -99      # last time we took damage
        self.last_dmg_by = None
        self.strafe_dir = 1
        self.strafe_until = 0
        self.stuck_t = 0.0
        self.unstick_until = -1
        self.unstick_dir = None
        self.last_pos = None
        self.cover = None
        self.cover_t = -99
        self.mode = "start"
        self.scan_phase = self.rng.random() * TAU
        self.crate_block = {}      # crate id -> time we gave up
        self.target_id = None
        mf.log(f"init {time.perf_counter() - t0:.2f}s")

    # ------------------------------------------------------------------ helpers
    def zone_at(self, z, tau):
        c, r = z["center"], z["radius"]
        nc, nr = z["next_center"], z["next_radius"]
        s0, s1 = z["shrink_start"], z["shrink_end"]
        if tau <= s0:
            return c, r
        if tau >= s1 or s1 <= s0:
            return nc, nr
        f = (tau - s0) / (s1 - s0)
        return [c[0] + (nc[0] - c[0]) * f, c[1] + (nc[1] - c[1]) * f], r + (nr - r) * f

    def safe_at(self, p, tau, margin=8.0):
        c, r = self.zone_at(self.z, tau)
        return d2(p, c) < r - margin

    def zone_home(self, p):
        z = self.z
        nc, nr = z["next_center"], z["next_radius"]
        d = d2(p, nc)
        keep = max(nr * 0.55, 0.0)
        if d <= keep:
            return None
        ux, uy = norm2(p[0] - nc[0], p[1] - nc[1])
        return (nc[0] + ux * keep, nc[1] + uy * keep)

    def mech_center(self, m):
        return (m["pos"][0], m["pos"][1], m["pos"][2] + 4.5)

    def muzzle(self):
        return (self.pos[0], self.pos[1], self.pos[2] + self.stats.get("muzzle_height", 7))

    def eye(self):
        return (self.pos[0], self.pos[1], self.pos[2] + self.stats.get("eye_height", 8))

    def aim_to(self, p, src=None):
        src = src or self.muzzle()
        dx, dy, dz = p[0] - src[0], p[1] - src[1], p[2] - src[2]
        return [math.atan2(dy, dx), math.atan2(dz, math.hypot(dx, dy))]

    def lead(self, p, v, speed):
        src = self.muzzle()
        q = p
        for _ in range(3):
            tt = math.dist(q, src) / speed
            q = (p[0] + v[0] * tt, p[1] + v[1] * tt, p[2] + v[2] * tt * 0.5)
        return q

    def weapon(self, kind):
        for i, w in enumerate(self.me["weapons"]):
            if w and w["kind"] == kind:
                return i, w
        return None, None

    # ------------------------------------------------------------------ movement
    def steer(self, want, speed=1.0):
        """Local avoidance: pick the walkable direction closest to `want`."""
        wx, wy = norm2(*want)
        if wx == 0 and wy == 0:
            return [0.0, 0.0]
        x, y = self.pos[0], self.pos[1]
        if not self.w.walkable(x, y):
            return [wx * speed, wy * speed]          # on a roof or a wall edge: just go
        base = math.atan2(wy, wx)
        for k in range(0, 9):
            for sgn in ((1,) if k == 0 else (1, -1)):
                a = base + sgn * k * math.pi / 8
                cx, cy = math.cos(a), math.sin(a)
                ok = True
                for dist in (3.0, 6.0):
                    if not self.w.walkable(x + cx * dist, y + cy * dist):
                        ok = False
                        break
                if ok:
                    return [cx * speed, cy * speed]
        return [wx * speed, wy * speed]

    def go_to(self, goal, speed=1.0):
        """Path toward goal; returns a move vector."""
        p = (self.pos[0], self.pos[1])
        if d2(p, goal) < 4:
            return [0.0, 0.0]
        if self.w.clear_walk(p, goal) and d2(p, goal) < 120:
            return self.steer((goal[0] - p[0], goal[1] - p[1]), speed)
        if (self.plan_goal is None or d2(self.plan_goal, goal) > 12 or self.t - self.plan_t > 6
                or (self.plan.path is None and self.plan.job is None)):
            self.plan.request(p, goal, self.t)
            self.plan_goal = goal
            self.plan_t = self.t
        path = self.plan.path
        if not path:
            return self.steer((goal[0] - p[0], goal[1] - p[1]), speed)
        # drop reached waypoints
        while len(path) > 1 and d2(p, path[0]) < 5:
            path.pop(0)
        # string pull: farthest visible waypoint among the next few
        tgt = path[0]
        for k in range(min(len(path), 14) - 1, 0, -1):
            if self.w.clear_walk(p, path[k]):
                tgt = path[k]
                break
        return self.steer((tgt[0] - p[0], tgt[1] - p[1]), speed)

    def unstick(self, move, out):
        """If we're pushing but not moving, jump/jet and wiggle."""
        sp = math.hypot(self.me["vel"][0], self.me["vel"][1])
        want = math.hypot(*move)
        if self.t < self.unstick_until:
            out["jump"] = True
            out["boost"] = self.me["energy"] > 15
            out["move"] = list(self.unstick_dir)
            return
        if want > 0.5 and sp < 1.5 and self.me["grounded"]:
            self.stuck_t += 0.1
        else:
            self.stuck_t = max(0.0, self.stuck_t - 0.05)
        if self.stuck_t > 0.7:
            self.stuck_t = 0
            a = math.atan2(move[1], move[0]) + self.rng.uniform(-0.8, 0.8)
            self.unstick_dir = (math.cos(a), math.sin(a))
            self.unstick_until = self.t + 1.4
            self.plan.path = None
            self.plan_goal = None

    # ------------------------------------------------------------------ perception
    def perceive(self, obs):
        t = self.t
        for m in obs["mechs"]:
            if m["kind"] != "mech" or m["id"] == self.me_id:
                continue
            self.enemies[m["id"]] = dict(m, t=t)
        for k in [k for k, e in self.enemies.items() if t - e["t"] > 8]:
            del self.enemies[k]
        for ev in obs["events"]:
            ty = ev.get("type")
            if ty == "damaged" and ev.get("by") is not None:
                self.last_hit_t = t
                self.last_dmg_by = ev["by"]
                if ev.get("bearing") is not None:
                    self.threats.append((t, ev["bearing"], 3.0))
            elif ty == "kill":
                self.enemies.pop(ev.get("target"), None)
        for s in obs["sounds"]:
            if s["kind"] in ("gun", "laser", "laser_charge", "lightning", "mortar", "step", "boost", "jet"):
                w = {"near": 2.0, "mid": 1.0, "far": 0.3}[s["dist"]]
                self.threats.append((t - s.get("age", 0), s["bearing"], w))
        self.threats = [th for th in self.threats if t - th[0] < 4][-30:]

    def threat_bearing(self):
        if not self.threats:
            return None
        sx = sy = 0.0
        for (tt, b, w) in self.threats:
            k = w * math.exp(-(self.t - tt))
            sx += math.cos(b) * k
            sy += math.sin(b) * k
        if math.hypot(sx, sy) < 0.2:
            return None
        return math.atan2(sy, sx)

    def pick_target(self, obs):
        best, bs = None, -1e9
        mz = self.muzzle()
        for m in obs["mechs"]:
            if m["kind"] != "mech":
                continue
            c = self.mech_center(m)
            dist = math.dist(c, mz)
            vis = self.w.los(mz, c) or (m["id"] == self.last_dmg_by and self.t - self.last_hit_t < 1.5)
            hpf = m.get("hp", 1400) / max(1, m.get("max_hp", 1400))
            s = -dist / 40.0 - m.get("hp", 1400) / 250.0 + (2.5 if vis else -3.0)
            if m["id"] == self.last_dmg_by and self.t - self.last_hit_t < 3:
                s += 1.5
            if m["id"] == self.target_id:
                s += 2.5
            if s > bs:
                bs, best = s, (m, vis, dist)
        return best

    # ------------------------------------------------------------------ weapons
    def shoot(self, tgt_pos, tgt_vel, vis, out, is_mech=True, radius=2.5):
        """Aim and fire everything sensible at a target point (center)."""
        me = self.me
        mz = self.muzzle()
        dist = math.dist(tgt_pos, mz)
        actual = me["aim"]
        fire = [False, False]
        gi, gun = self.weapon("gun")
        li, lit = self.weapon("lightning")
        ri, las = self.weapon("laser")
        mi, mor = self.weapon("mortar")
        aim = None

        # laser: charge while roughly on target, release when full and dead on
        laser_release = False
        if las is not None and is_mech and vis and dist < las["stats"].get("range", 280) * 0.95:
            full = las["stats"].get("charge", 0.75)
            ch = las.get("charge", 0) or 0
            exact = self.aim_to(tgt_pos)
            if ch >= full - 0.06:
                aim = exact
                err = abs(wrap(actual[0] - exact[0])) + abs(actual[1] - exact[1])
                if err < max(math.atan2(radius * 0.7, dist), 0.01):
                    laser_release = True          # fire = False releases the beam
                else:
                    fire[ri] = True
            elif las["cooldown"] <= 0.05:
                err = abs(wrap(actual[0] - exact[0]))
                if err < 0.4:
                    fire[ri] = True

        if aim is None:
            if gun is not None and vis:
                p = self.lead(tgt_pos, tgt_vel, gun["stats"]["speed"])
            else:
                p = tgt_pos
            aim = self.aim_to(p)

        # mortar: lob when no line of sight (or no gun), not too close
        if mor is not None and (not vis or gun is None) and 25 < dist < 220 and is_mech:
            ma = self.mortar_aim(tgt_pos, tgt_vel, mor["stats"], high=not vis)
            if ma is not None:
                aim = ma
                err = abs(wrap(actual[0] - ma[0])) + abs(actual[1] - ma[1])
                if err < 0.04 and mor["cooldown"] <= 0:
                    fire[mi] = True

        err_y = abs(wrap(actual[0] - aim[0]))
        err_p = abs(actual[1] - aim[1])
        tol = max(math.atan2(radius * 0.8, max(dist, 1)), 0.012)
        if gun is not None and vis and dist < min(gun["stats"]["range"], 140 if is_mech else 120) and err_y < tol and err_p < tol * 1.5:
            if not (las is not None and fire[ri] and (las.get("charge", 0) or 0) > 0.5):
                fire[gi] = True
        if lit is not None and vis and dist < lit["stats"]["range"] * 0.95 and lit["cooldown"] <= 0:
            cone = math.radians(lit["stats"]["cone_deg"]) * 0.4
            if err_y < cone and me["energy"] > lit["stats"]["energy"] + 6:
                fire[li] = True
        if laser_release:
            fire[ri] = False
        out["aim"] = aim
        out["fire"] = fire
        if gun is not None and gun.get("ammo", 1) == 0 and gun.get("reload", 0) == 0:
            out["reload"] = True

    def mortar_aim(self, p, v, st, high):
        src = (self.pos[0], self.pos[1], self.pos[2] + self.stats.get("height", 9) + 0.5)
        spd, g = st["speed"], st["gravity"]
        q = p
        for _ in range(3):
            dx, dy = q[0] - src[0], q[1] - src[1]
            d = math.hypot(dx, dy)
            dz = (q[2] - 4.0) - src[2]
            disc = spd ** 4 - g * (g * d * d + 2 * dz * spd * spd)
            if disc < 0 or d < 1:
                return None
            r = math.sqrt(disc)
            th = math.atan2(spd * spd + (r if high else -r), g * d)
            tt = d / (spd * math.cos(th))
            q = (p[0] + v[0] * tt, p[1] + v[1] * tt, p[2])
        th = max(self.cfg["mech"]["min_pitch"], min(self.cfg["mech"]["max_pitch"] - 0.02, th))
        return [math.atan2(dy, dx), th]

    def manage_loot(self, obs, out):
        """Take better weapons we're standing next to."""
        reach = self.cfg["loot"]["pickup_radius"] - 0.3
        w = self.me["weapons"]
        held = {x["kind"] for x in w if x}
        for p in obs["pickups"]:
            if abs(p["pos"][0] - self.pos[0]) > reach + 2 or abs(p["pos"][1] - self.pos[1]) > reach + 2:
                continue
            if d2(p["pos"], self.pos) > reach or abs(p["pos"][2] - self.pos[2]) > 3.8:
                continue
            k = p["kind"]
            if k in held or k == "gun":
                continue
            if w[1] is None:
                out["take"] = p["id"]
                return
            cur = w[1]["kind"]
            if WEAPON_RANK[k] > WEAPON_RANK.get(cur, 0) + 0.2 and not (cur == "laser" and (w[1].get("charge") or 0) > 0):
                out["take"] = p["id"]
                out["drop"] = 1
                return

    def want_pickup(self, p):
        w = self.me["weapons"]
        held = {x["kind"] for x in w if x}
        k = p["kind"]
        if k in held or k == "gun":
            return False
        if w[1] is None:
            return True
        return WEAPON_RANK[k] > WEAPON_RANK.get(w[1]["kind"], 0) + 0.2

    # ------------------------------------------------------------------ cover
    def find_cover(self, threats):
        p = self.pos
        best, bd = None, 1e9
        t_end = time.perf_counter() + 0.008
        for r in (8, 14, 22, 32, 44, 58):
            if time.perf_counter() > t_end:
                break
            for k in range(16):
                a = k * TAU / 16
                q = (p[0] + math.cos(a) * r, p[1] + math.sin(a) * r)
                if not self.w.walkable(*q):
                    continue
                if not self.safe_at(q, self.t + 15, 5):
                    continue
                gz = self.w.ground(*q)
                top = (q[0], q[1], gz + 9)
                hidden = True
                for th in threats:
                    if self.w.los(th, top, skip_a=2, skip_b=1):
                        hidden = False
                        break
                if hidden and self.w.clear_walk(p, q):
                    d = r
                    if d < bd:
                        bd, best = d, q
            if best:
                return best
        return best

    # ------------------------------------------------------------------ main
    def act(self, obs):
        tick_start = time.perf_counter()
        self.obs = obs
        self.me = me = obs["self"]
        self.pos = me["pos"]
        self.stats = me["stats"]
        self.t = t = obs["t"]
        self.z = obs["zone"]
        out = {}
        self.perceive(obs)

        if obs.get("offer"):
            ch = obs["offer"]["choices"]
            ids = [c["id"] for c in ch]
            best = min(range(len(ids)), key=lambda i: UPGRADE_PREF.index(ids[i]) if ids[i] in UPGRADE_PREF else 99)
            out["pick"] = best

        hpf = me["hp"] / max(1, me["max_hp"])
        pos2 = (self.pos[0], self.pos[1])
        target = self.pick_target(obs)
        tb = self.threat_bearing()
        home = self.zone_home(pos2)
        zone_ok_soon = self.safe_at(pos2, t + 12, 10)
        move = [0.0, 0.0]
        boost = False
        mode = "roam"

        # incoming mortar shells: sidestep
        dodge = self.mortar_dodge(obs)

        visible_mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        hurt = hpf < 0.38 or (hpf < 0.55 and len(visible_mechs) >= 2)
        if target is not None:
            tm = target[0]
            thpf = tm.get("hp", 1400) / max(1, tm.get("max_hp", 1400))
            # finishing a kill beats running; running from someone on top of us doesn't work
            if (thpf < 0.25 and thpf * tm.get("max_hp", 1400) < me["hp"]) or target[2] < 30:
                hurt = False
        else:
            thpf = 1.0

        if not me["in_zone"] or not zone_ok_soon:
            mode = "zone"
            goal = self.pick_spot()
            move = self.go_to(goal)
            boost = (not me["in_zone"]) or not self.safe_at(pos2, t + 5, 4)
        elif hurt and (target is not None or t - self.last_hit_t < 2.5) and self.have_cover(t):
            mode = "hide"
            move = self.go_to(self.cover)
            boost = me["energy"] > 25
        elif target is not None and target[1] and target[2] > self.engage_max() + 10 and thpf > 0.35 and self.have_cover(t):
            mode = "evade"
            move = self.go_to(self.cover)
            boost = me["energy"] > 40
        elif target is not None and (target[1] or target[2] < self.engage_max()):
            mode = "fight"
            m, vis, dist = target
            self.target_id = m["id"]
            pref = self.pref_range(m, hpf)
            ax, ay = norm2(m["pos"][0] - self.pos[0], m["pos"][1] - self.pos[1])
            if t > self.strafe_until:
                self.strafe_dir = self.rng.choice((-1, 1))
                self.strafe_until = t + self.rng.uniform(0.5, 1.4)
            radial = 0.0
            if dist > pref + 15:
                radial = 0.8
            elif dist < pref - 10:
                radial = -0.8
            if not vis:
                # lost sight: close in along a path
                move = self.go_to((m["pos"][0], m["pos"][1]))
            else:
                move = self.steer((ax * radial - ay * self.strafe_dir, ay * radial + ax * self.strafe_dir))
            # don't strafe out of the zone
            nxt = (self.pos[0] + move[0] * 15, self.pos[1] + move[1] * 15)
            if not self.safe_at(nxt, t + 8, 6):
                move = self.go_to(home or tuple(self.z["next_center"]))
        elif t - self.last_hit_t < 2.5 and self.have_cover(t):
            mode = "evade"
            move = self.go_to(self.cover)
            boost = me["energy"] > 40
        else:
            mode, move = self.idle_goal(obs, hpf)

        if dodge is not None:
            move = self.steer(dodge)
            boost = me["energy"] > 20

        # ----- aim / fire
        if target is not None:
            m, vis, dist = target
            self.shoot(self.mech_center(m), m["vel"], vis, out)
        else:
            self.idle_aim(obs, move, tb, out)

        self.manage_loot(obs, out)

        # quiet walking when nothing is going on
        if mode in ("roam", "camp") and not boost:
            sp = self.cfg["sound"]["step_speed"] / max(1, self.stats.get("walk_speed", 11.7))
            move = [move[0] * sp * 0.92, move[1] * sp * 0.92]

        out["move"] = move
        out["boost"] = bool(boost)
        self.unstick(move, out)

        # use leftover time for path search
        spent = time.perf_counter() - tick_start
        self.plan.step(max(0.0, 0.010 - spent))
        self.mode = mode
        mf.draw.label(f"{mode} hp{hpf:.2f} e{me['energy']:.0f}")
        return out

    def have_cover(self, t):
        if t - self.cover_t > 1.0:
            threats = [self.mech_center(e) for e in self.enemies.values() if t - e["t"] < 4]
            tb = self.threat_bearing()
            if not threats and tb is not None:
                threats = [(self.pos[0] + math.cos(tb) * 60, self.pos[1] + math.sin(tb) * 60, self.pos[2] + 6)]
            self.cover = self.find_cover(threats) if threats else None
            self.cover_t = t
        return self.cover is not None

    def engage_max(self):
        if self.weapon("laser")[1] is not None:
            return 190
        return 75

    def pref_range(self, m, hpf):
        if self.weapon("lightning")[1] is not None and hpf > 0.5:
            return 28
        if self.weapon("laser")[1] is not None:
            return 80
        return 55

    def mortar_dodge(self, obs):
        g = self.cfg["weapons"]["mortar"]["gravity"]
        R = self.cfg["weapons"]["mortar"]["splash_radius"] + 4
        p = self.pos
        for pr in obs["projectiles"]:
            if pr["kind"] != "mortar":
                continue
            x, y, z = pr["pos"]
            vx, vy, vz = pr["vel"]
            # time to fall to our height
            zz = z - p[2]
            disc = vz * vz + 2 * g * zz
            if disc < 0:
                continue
            tt = (vz + math.sqrt(disc)) / g
            lx, ly = x + vx * tt, y + vy * tt
            if math.hypot(lx - p[0], ly - p[1]) < R and tt < 3.5:
                ax, ay = norm2(p[0] - lx, p[1] - ly)
                if ax == 0 and ay == 0:
                    ax, ay = norm2(-vy, vx)
                return (ax, ay)
            # direct shot along the ground toward us
        for pr in obs["projectiles"]:
            if pr["kind"] not in ("gun",):
                continue
        return None

    def idle_goal(self, obs, hpf):
        """Nothing to fight: loot cubes/weapons, farm drones/crates, capture drops, drift to zone."""
        t = self.t
        p = (self.pos[0], self.pos[1])
        cands = []
        for c in obs["cubes"]:
            cands.append((d2(c["pos"], p) - 25, (c["pos"][0], c["pos"][1]), "cube"))
        for pk in obs["pickups"]:
            if self.want_pickup(pk) and abs(pk["pos"][2] - self.w.ground(*pk["pos"][:2])) < 2:
                cands.append((d2(pk["pos"], p) - 15, (pk["pos"][0], pk["pos"][1]), "loot"))
        for dr in obs.get("drops", []):
            dd = d2(dr["pos"], p)
            if dd < 180 and dr["lands_in"] < dd / 11 + 4:
                bonus = 40 if hpf < 0.7 else 0
                others = dr.get("holder") not in (None, self.me_id)
                if not others:
                    cands.append((dd - 30 - bonus, (dr["pos"][0], dr["pos"][1]), "drop"))
        best = None
        for c in sorted(cands):
            if c[0] > 90:
                break
            if self.safe_at(c[1], t + d2(c[1], p) / 10 + 10, 6) and self.w.walkable(*c[1]):
                best = c
                break
        if best:
            sp = 1.0
            return best[2], self.go_to(best[1], sp)
        spot = self.pick_spot()
        if d2(spot, p) < 3:
            return "camp", [0.0, 0.0]
        return "roam", self.go_to(spot)

    def pick_spot(self):
        """A tucked-in spot (short sight lines) that stays safe through the next shrink."""
        t = self.t
        p = (self.pos[0], self.pos[1])
        z = self.z
        key = (round(z["next_center"][0]), round(z["next_center"][1]), z["next_radius"])
        sp = getattr(self, "spot", None)
        if sp is not None and self.spot_key == key and self.safe_at(sp, z["shrink_end"], 10):
            return sp
        nc, nr = z["next_center"], z["next_radius"]
        best, bs = None, 1e9
        for k in range(90):
            a = self.rng.random() * TAU
            r = math.sqrt(self.rng.random()) * max(nr * 0.75, 3)
            q = (nc[0] + math.cos(a) * r, nc[1] + math.sin(a) * r)
            if not self.w.walkable(*q):
                continue
            sc = self.w.exposure(*q) + 0.25 * d2(q, p) + 0.15 * d2(q, nc)
            if sc < bs:
                bs, best = sc, q
        if best is None:
            best = tuple(nc)
        self.spot, self.spot_key = best, key
        return best

    def idle_aim(self, obs, move, tb, out):
        """No mech in view: shoot crates/drones if it's quiet, else look toward threats or scan."""
        t = self.t
        hpf = self.me["hp"] / max(1, self.me["max_hp"])
        mz = self.muzzle()
        quiet = t - self.last_hit_t > 4
        # NPC drones and crates for cubes
        if quiet and hpf > 0.45:
            best = None
            for m in obs["mechs"]:
                if m["kind"] == "npc":
                    c = (m["pos"][0], m["pos"][1], m["pos"][2] + 1.75)
                    dd = math.dist(c, mz)
                    if dd < 140 and self.w.los(mz, c):
                        best = (dd, c, m["vel"], 1.8)
                        break
            if best is None:
                for cr in obs["crates"]:
                    c = (cr["pos"][0], cr["pos"][1], cr["pos"][2] + 1.2)
                    dd = math.dist(c, mz)
                    if dd < 120 and self.w.los(mz, c):
                        if best is None or dd < best[0]:
                            best = (dd, c, (0, 0, 0), 1.4)
            if best is not None:
                self.shoot(best[1], best[2], True, out, is_mech=False, radius=best[3])
                # don't waste the laser on crates
                ri, _ = self.weapon("laser")
                mi, _ = self.weapon("mortar")
                for idx in (ri, mi):
                    if idx is not None:
                        out["fire"][idx] = False
                return
        # remembered enemies (behind cover)
        recent = [e for e in self.enemies.values() if t - e["t"] < 5]
        if recent:
            e = min(recent, key=lambda e: d2(e["pos"], self.pos))
            out["aim"] = self.aim_to(self.mech_center(e))
            return
        if tb is not None:
            out["aim"] = [tb, 0.0]
            return
        # scan around travel direction (or toward zone center)
        if math.hypot(*move) > 0.1:
            base = math.atan2(move[1], move[0])
        else:
            nc = self.z["next_center"]
            base = math.atan2(nc[1] - self.pos[1], nc[0] - self.pos[0])
        sweep = math.sin(t * 0.9 + self.scan_phase) * 1.4
        out["aim"] = [wrap(base + sweep), 0.0]


if __name__ == "__main__":
    mf.run(Bot)
