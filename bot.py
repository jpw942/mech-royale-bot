"""Cockroach: a survival-first Mech Royale bot.
Stay in the zone, fight from the front, break line of sight to regen, farm cubes when it's quiet."""
import math
import random
import time

import mechfight as mf
from world import World, Planner

STRATEGY = "snipe"     # "snipe" (v7 experiment), "brawl2" (v6), "brawl" (v5), "fight" (v3), "survive" (v4)

TAU = 2 * math.pi
WEAPON_RANK = {"lightning": 3.0, "laser": 2.5, "mortar": 1.0, "gun": 0.0}
# Upgrade picks: survivability first, then upgrades for the weapons we hold (best weapon first),
# then general utility, then upgrades for weapons we don't hold.
SURVIVAL_UPS = ["armor", "plating", "nanites"]
WEAPON_UPS = {
    "lightning": ["lightning_efficiency", "lightning_range", "lightning_chain"],
    "laser": ["laser_damage", "laser_charge", "laser_pierce"],
    "gun": ["gun_rate", "gun_damage", "gun_mag", "gun_velocity"],
    "mortar": ["mortar_reload", "mortar_velocity", "mortar_splash"],
}
GENERAL_UPS = ["gyros", "servos", "capacitor", "reactor", "sensors", "jets"]
UPGRADE_PREF = SURVIVAL_UPS + [u for k in ("lightning", "laser", "gun", "mortar") for u in WEAPON_UPS[k]] + GENERAL_UPS

# Early game: practice hunters trickle in one at a time, so the opening is a safe window to gear up.
RUSH_T = 90.0          # weapon rush until this time (or until slot 1 holds lightning/laser)
RUSH_BONUS = {"lightning": 200.0, "laser": 170.0, "mortar": 60.0}
RUSH_MAX_D = 120.0     # rush pickups up to this far
ROOF_MAX_H = 15.0      # rooftop pickups up to this high (jump + boost)
FARM_T = 120.0         # actively hunt drones/crates until this time
FARM_MAX_D = 150.0
EXPLORE_T = 120.0
EXPLORE_CELL = 50.0

# v3: fight smarter
COMMIT_HPF = 0.45      # commit to a kill below this est hp fraction
COMMIT_MEMORY = 8.0    # enemies are remembered (and chased) this long
COMMIT_REBLOCK = 3.0   # after dropping a commitment, don't re-commit to the same id this long
AMBUSH_EXPO = 115.0    # world.exposure above this (median walkable cell) counts as exposed
DUCK_COVER_D = 12.0    # laser duck only to cover this close
LASER_PEEK_S = 1.7     # stay ducked this long after a laser shot
HEAL_START = 0.85
HEAL_DONE = 0.90

# v4 experiment: pure survival (STRATEGY == "survive")
SURVIVE_UPS = ["armor", "plating", "nanites", "sensors"]
S_IGNORE_D = 60.0      # visible enemies farther than this (and not hurting us) are only watched
S_CLOSE_D = 35.0       # an enemy this close gets fought
S_FINISH_HPF = 0.30    # keep fighting a visible enemy below this hp fraction
S_LOOT_D = 25.0
S_CUBE_D = 15.0
S_HEAL_DONE = 0.90
S_LEAVE_MARGIN = 15.0  # leave for the next spot when walk time + this would pass the next shrink
S_SWEEP = math.radians(70)

# v5: brawl (STRATEGY == "brawl")
B_RUSH_BONUS = {"lightning": 400.0, "laser": 170.0, "mortar": 0.0}
B_RUSH_MAX_D = {"lightning": 150.0, "laser": 120.0, "mortar": 40.0}
B_MORTAR_UNTIL = 75.0          # don't take a mortar before this (something better usually turns up)
B_LIGHTNING_BAND = (25.0, 35.0)
B_GUN_PREF = 40.0
B_ZIG = (0.4, 0.9)             # strafe flip interval while closing
B_BOOST_CLOSE_D = 60.0
B_MIN_FIGHT_ENERGY = 25.0      # keep this much for lightning: no boosting below it in a fight
B_LASER_MIN_D = 45.0
B_DUCK_D = 10.0
B_PUNISH_S = 1.6
B_HUNT_T = 90.0
B_ENGAGE_D = 150.0             # in view and closer than this: go fight it
B_LOOT_AFTER_KILL_S = 15.0
B_LOOT_AFTER_KILL_D = 40.0
B_HIDE_HPF = 0.35
B_HEAL_START = 0.70
B_FIGHT_MODES = ("fight", "commit", "chase", "hunt", "jink", "punish", "duck")

# v6: brawl2 (everything in brawl, plus these)
# loadout value by the pair of kinds held (None = empty slot); swaps only go up this table
B2_LOADOUT = {
    frozenset(["lightning", "laser"]): 10, frozenset(["lightning", "mortar"]): 7,
    frozenset(["laser", "gun"]): 7, frozenset(["lightning", "gun"]): 6,
    frozenset(["laser", "mortar"]): 5, frozenset(["gun", "mortar"]): 2,
    frozenset(["lightning"]): 5, frozenset(["laser"]): 4, frozenset(["gun"]): 1, frozenset(["mortar"]): 1.5,
    frozenset(): 0,
}
B2_SWAP_FIGHT_D = 60.0       # mid-fight, only swap weapons if the target is farther than this
B2_LIGHTNING_D = 40.0        # lightning range band for shoot()
B2_SNOWBALL_T = 60.0         # farm drones/crates until this, or until the first enemy mech is seen
B2_FARM_LIGHTNING_BAND = (25.0, 38.0)   # drone stand-off when lightning is our weapon
B2_BAD_CHARGE_MODES = ("jink", "punish", "duck", "chase", "hunt", "zone", "hide", "evade")
# v7: snipe (everything in brawl, except these)
SN_RANK = {"laser": 3.0, "lightning": 2.5, "mortar": 1.0, "gun": 0.0}   # for the non-gun slot
SN_RUSH_BONUS = {"laser": 400.0, "lightning": 170.0, "mortar": 0.0}
SN_RUSH_MAX_D = {"laser": 150.0, "lightning": 120.0, "mortar": 40.0}
SN_MIN_D = 60.0              # long range band for peek-sniping
SN_MAX_D = 250.0
SN_GUN_MAX_D = 100.0         # in snipe mode, the gun only fires inside this
SN_SWAP_FIGHT_D = 60.0
SN_REPLAN_S = 4.0            # recompute cover/peek spots this often
SN_PEEK_R = (3.0, 5.0, 8.0, 11.0)
B2_UPS = {
    "lightning": ["lightning_efficiency", "lightning_chain"],
    "laser": ["laser_damage", "laser_charge"],
    "gun": ["gun_damage", "gun_rate"],
    "mortar": ["mortar_reload"],
}


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
        self.last_mech_hit_t = -99  # last damage from a mech (not NPC drones or the zone)
        self.loot_block = {}       # pickup id -> time we gave up on it
        self.loot_chase = (None, 0.0)
        self.farm_chase = (None, 0.0)
        self.farm_tgt = None       # set by idle_goal when farming, consumed by idle_aim
        self.idle_jump = False     # idle_goal wants jump + boost (rooftop pickup)
        self.cur_mode = "start"
        self.seen = set()          # explored EXPLORE_CELL cells
        self.explore_goal = None
        self.explore_until = -1
        # v3 state
        self.dmg_dealt = {}        # id -> total damage we've dealt it
        self.last_hit_on = {}      # id -> last time we hit it
        self.last_attack_by = {}   # id -> last time it damaged us (it isn't regenerating then)
        self.commit_id = None
        self.commit_block = {}     # id -> time we dropped a commitment
        self.last_kill_t = -99
        self.last_enemy_seen_t = -99
        self.laser_fire_t = -99    # last laser shot heard or seen (charging stopped)
        self.charge_seen_t = -99   # last time a visible enemy was charging a laser
        self.rear_hit = None       # (t, bearing) of the last hit from behind
        self.healing = False
        self.heal_spot = None
        mcfg = self.cfg.get("mech", {})
        self.regen_delay = mcfg.get("regen_delay", 5)
        self.regen_per_s = mcfg.get("regen_per_s", 45)
        # v4 survive state
        self.s_spot = None
        self.s_hiding = False
        self.s_hide_spot = None
        self.s_look = None         # (spot, yaw) most exposed direction from the current spot
        # v5 brawl state
        self.brawl = STRATEGY in ("brawl", "brawl2", "snipe")
        self.brawl2 = STRATEGY == "brawl2"
        self.snipe = STRATEGY == "snipe"
        self.sn = None             # peek plan: dict(tid, tpos, t, cover, peek)
        self.sn_phase = ""
        self.cur_target_d = None
        self.zig_dir = 1
        self.zig_until = 0.0
        self.hunt_id = None
        mf.log(f"strategy {STRATEGY}")
        self.up_ids = [u["id"] for u in self.cfg.get("upgrades", [])]
        mf.log(f"upgrades in config: {self.up_ids}")
        unknown = [u for u in UPGRADE_PREF if u not in self.up_ids]
        unranked = [u for u in self.up_ids if u not in UPGRADE_PREF]
        if unknown or unranked:
            mf.log(f"upgrade pref mismatch: not in config {unknown}, not ranked {unranked}")
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
        # events first, so damage dealt this tick is counted before we store the hp we see now
        for ev in obs["events"]:
            ty = ev.get("type")
            if ty == "damaged" and ev.get("by") is not None:
                self.last_hit_t = t
                self.last_dmg_by = ev["by"]
                self.last_attack_by[ev["by"]] = t
                if ev.get("weapon") not in ("npc", "zone"):
                    self.last_mech_hit_t = t
                    if ev.get("rear") and ev.get("bearing") is not None:
                        self.rear_hit = (t, ev["bearing"])
                if ev.get("bearing") is not None:
                    self.threats.append((t, ev["bearing"], 3.0))
            elif ty == "hit":
                tid = ev.get("target")
                self.dmg_dealt[tid] = self.dmg_dealt.get(tid, 0) + ev.get("amount", 0)
                self.last_hit_on[tid] = t
            elif ty == "kill":
                tid = ev.get("target")
                self.enemies.pop(tid, None)
                self.last_kill_t = t
                if tid == self.commit_id:
                    mf.log(f"t{t:.1f} killed committed {tid}")
                    self.commit_id = None
        for m in obs["mechs"]:
            if m["kind"] != "mech" or m["id"] == self.me_id:
                continue
            prev = self.enemies.get(m["id"])
            lf = prev.get("laser_fire_t", -99) if prev is not None else -99
            if m.get("charging"):
                self.charge_seen_t = t
            elif prev is not None and prev.get("charging") and t - prev["t"] < 0.5:
                self.laser_fire_t = t                    # it stopped charging: it fired
                lf = t
            self.enemies[m["id"]] = dict(m, t=t, dmg_at_seen=self.dmg_dealt.get(m["id"], 0), laser_fire_t=lf)
        for k in [k for k, e in self.enemies.items() if t - e["t"] > COMMIT_MEMORY]:
            del self.enemies[k]
        for s in obs["sounds"]:
            if s["kind"] == "laser":
                self.laser_fire_t = max(self.laser_fire_t, t - s.get("age", 0))
                # per-enemy: credit the shot to a known enemy on that bearing (within ~15 deg)
                for e in self.enemies.values():
                    b = math.atan2(e["pos"][1] - self.pos[1], e["pos"][0] - self.pos[0])
                    if abs(wrap(b - s["bearing"])) < 0.26:
                        e["laser_fire_t"] = max(e.get("laser_fire_t", -99), t - s.get("age", 0))
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
            est = self.est_hp(m["id"])
            if est is None:
                est = m.get("hp", 1400)
            s = -dist / 40.0 - est / 250.0 + (2.5 if vis else -3.0)
            if m["id"] == self.last_dmg_by and self.t - self.last_hit_t < 3:
                s += 1.5
            if m["id"] == self.target_id:
                s += 2.5
            if m["id"] == self.commit_id:
                s += 5.0
            if s > bs:
                bs, best = s, (m, vis, dist)
        return best

    # ------------------------------------------------------------------ weapons
    def shoot(self, tgt_pos, tgt_vel, vis, out, is_mech=True, radius=2.5):
        """Aim and fire everything sensible at a target point (center)."""
        if self.brawl2:
            return self.shoot_b2(tgt_pos, tgt_vel, vis, out, is_mech, radius)
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

    def shoot_b2(self, tgt_pos, tgt_vel, vis, out, is_mech=True, radius=2.5):
        """brawl2 shooting; works with any loadout (the gun may be gone, slots looked up by kind).
        <= 40 m: lightning, laser only when charging won't slow us at a bad moment.
        40-280 m: laser, aimed dead center (hitscan, no lead), released when full and on target."""
        me = self.me
        mz = self.muzzle()
        dist = math.dist(tgt_pos, mz)
        actual = me["aim"]
        fire = [False] * max(2, len(me["weapons"]))
        gi, gun = self.weapon("gun")
        li, lit = self.weapon("lightning")
        ri, las = self.weapon("laser")
        mi, mor = self.weapon("mortar")
        aim = None
        lit_ok = lit is not None and vis and dist < lit["stats"]["range"] * 0.95

        laser_release = False
        if las is not None and is_mech and vis and dist < las["stats"].get("range", 280) * 0.95:
            full = las["stats"].get("charge", 0.75)
            ch = las.get("charge", 0) or 0
            close = dist <= B2_LIGHTNING_D
            start_ok = self.cur_mode not in B2_BAD_CHARGE_MODES and \
                (not close or lit is None or dist <= B_LIGHTNING_BAND[1])
            if ch > 0 or start_ok:
                exact = self.aim_to(tgt_pos)
                aim = exact
                if ch >= full - 0.06:
                    err = abs(wrap(actual[0] - exact[0])) + abs(actual[1] - exact[1])
                    if err < max(math.atan2(radius * 0.7, dist), 0.01):
                        laser_release = True      # fire = False releases the beam
                    else:
                        fire[ri] = True
                elif ch > 0:
                    fire[ri] = True               # keep charging
                elif las["cooldown"] <= 0.05 and abs(wrap(actual[0] - exact[0])) < 0.4:
                    fire[ri] = True

        if aim is None:
            p = self.lead(tgt_pos, tgt_vel, gun["stats"]["speed"]) if gun is not None and vis else tgt_pos
            aim = self.aim_to(p)

        # mortar: lob when there's no line of sight, or nothing else reaches
        if mor is not None and is_mech and 25 < dist < 220 and (not vis or (gun is None and las is None and not lit_ok)):
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
        if lit_ok and lit["cooldown"] <= 0 and me["energy"] > lit["stats"]["energy"] + 6:
            half = math.radians(lit["stats"]["cone_deg"]) / 2
            # whenever the target is inside the cone (measured from the real target, not a mortar arc)
            ex = self.aim_to(tgt_pos)
            if abs(wrap(actual[0] - ex[0])) < half * 0.9 and abs(actual[1] - ex[1]) < half:
                fire[li] = True
        if laser_release:
            fire[ri] = False
        out["aim"] = aim
        out["fire"] = fire[:2]
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

    def loadout_value(self, kinds):
        return B2_LOADOUT.get(frozenset(k for k in kinds if k), 0)

    def best_swap(self, kind):
        """brawl2: (slot to fill/replace, new value) if taking `kind` improves the loadout, else None.
        Slots are looked up per index; never replaces a charging laser."""
        w = self.me["weapons"]
        cur = [x["kind"] if x else None for x in w]
        if kind in cur:
            return None
        if self.brawl and kind == "mortar" and self.t < B_MORTAR_UNTIL:
            return None
        base = self.loadout_value(cur)
        best = None
        for s in range(len(w)):
            if w[s] is not None and w[s]["kind"] == "laser" and (w[s].get("charge") or 0) > 0:
                continue
            new = list(cur)
            new[s] = kind
            v = self.loadout_value(new)
            # prefer filling an empty slot over replacing at equal value
            if v > base + 0.1 and (best is None or v > best[1] or (v == best[1] and w[s] is None)):
                best = (s, v)
        return best

    def manage_loot(self, obs, out):
        """Take better weapons we're standing next to."""
        reach = self.cfg["loot"]["pickup_radius"] - 0.3
        if self.brawl2:
            if self.cur_target_d is not None and self.cur_target_d <= B2_SWAP_FIGHT_D:
                return                                   # mid-fight up close: no 1 s equip pause
            for p in obs["pickups"]:
                if d2(p["pos"], self.pos) > reach or abs(p["pos"][2] - self.pos[2]) > 3.8:
                    continue
                sw = self.best_swap(p["kind"])
                if sw is None:
                    continue
                out["take"] = p["id"]
                if self.me["weapons"][sw[0]] is not None:
                    out["drop"] = sw[0]
                    mf.log(f"t{self.t:.1f} swap slot {sw[0]} {self.me['weapons'][sw[0]]['kind']} -> {p['kind']}")
                return
            return
        if self.snipe and self.cur_target_d is not None and self.cur_target_d <= SN_SWAP_FIGHT_D:
            return                                       # snipe: no swapping mid-fight
        w = self.me["weapons"]
        held = {x["kind"] for x in w if x}
        sp = self.special_slot()
        rank = SN_RANK if self.snipe else WEAPON_RANK
        for p in obs["pickups"]:
            if abs(p["pos"][0] - self.pos[0]) > reach + 2 or abs(p["pos"][1] - self.pos[1]) > reach + 2:
                continue
            if d2(p["pos"], self.pos) > reach or abs(p["pos"][2] - self.pos[2]) > 3.8:
                continue
            k = p["kind"]
            if k in held or k == "gun":
                continue
            if self.brawl and k == "mortar" and self.t < B_MORTAR_UNTIL:
                continue
            if w[sp] is None:
                out["take"] = p["id"]
                return
            cur = w[sp]["kind"]
            if rank[k] > rank.get(cur, 0) + 0.2 and not (cur == "laser" and (w[sp].get("charge") or 0) > 0):
                out["take"] = p["id"]
                out["drop"] = sp
                return

    def special_slot(self):
        """The slot we put found weapons in: 1, or for snipe whichever slot isn't the gun (never drop it)."""
        if self.snipe:
            gi, _ = self.weapon("gun")
            if gi is not None:
                return 1 - gi
        return 1

    def want_pickup(self, p):
        if self.brawl2:
            return self.best_swap(p["kind"]) is not None
        w = self.me["weapons"]
        held = {x["kind"] for x in w if x}
        k = p["kind"]
        if k in held or k == "gun":
            return False
        if self.brawl and k == "mortar" and self.t < B_MORTAR_UNTIL:
            return False
        sp = self.special_slot()
        rank = SN_RANK if self.snipe else WEAPON_RANK
        if w[sp] is None:
            return True
        return rank[k] > rank.get(w[sp]["kind"], 0) + 0.2

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
        self.farm_tgt = None
        self.idle_jump = False
        cx, cy = int(self.pos[0] // EXPLORE_CELL), int(self.pos[1] // EXPLORE_CELL)
        if (cx, cy) not in self.seen:
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    self.seen.add((cx + di, cy + dj))

        if obs.get("offer"):
            ch = obs["offer"]["choices"]
            ids = [c["id"] for c in ch]
            rank = self.upgrade_rank()
            best = min(range(len(ids)), key=lambda i: rank.get(ids[i], 999))
            out["pick"] = best

        if STRATEGY == "survive":
            return self.act_survive(obs, out, tick_start)

        hpf = me["hp"] / max(1, me["max_hp"])
        pos2 = (self.pos[0], self.pos[1])
        visible_mechs = [m for m in obs["mechs"] if m["kind"] == "mech" and m["id"] != self.me_id]
        if visible_mechs:
            self.last_enemy_seen_t = t
        self.update_commit(obs)
        self.update_heal(hpf)
        target = self.pick_target(obs)
        self.cur_target_d = target[2] if target is not None else None
        tb = self.threat_bearing()
        home = self.zone_home(pos2)
        zone_ok_soon = self.safe_at(pos2, t + 12, 10)
        move = [0.0, 0.0]
        boost = False
        mode = "roam"

        # incoming mortar shells: sidestep
        dodge = self.mortar_dodge(obs)

        if self.brawl:
            hurt = hpf < B_HIDE_HPF
            self.update_hunt(hpf)
        else:
            hurt = hpf < 0.45 or (hpf < 0.55 and len(visible_mechs) >= 2)
        if target is not None:
            tm = target[0]
            thpf = tm.get("hp", 1400) / max(1, tm.get("max_hp", 1400))
            # finishing a kill beats running; running from someone on top of us doesn't work
            if (thpf < 0.25 and thpf * tm.get("max_hp", 1400) < me["hp"]) or target[2] < 30:
                hurt = False
        else:
            thpf = 1.0

        in_zone_ok = me["in_zone"] and zone_ok_soon
        snipe_step = self.snipe_step(obs, target, hpf) if in_zone_ok and self.snipe else None
        if snipe_step is None:
            self.sn_phase = ""
        laser_step = self.brawl_laser(obs) if in_zone_ok and self.brawl and snipe_step is None else None
        duck = self.duck_cover(obs) if in_zone_ok and not self.brawl else None
        commit_step = self.commit_step(obs, target, hpf, home) \
            if in_zone_ok and duck is None and laser_step is None and snipe_step is None else None
        hunt_step = self.hunt_step(target) if in_zone_ok and self.brawl else None

        if not in_zone_ok:
            mode = "zone"
            goal = self.pick_spot()
            move = self.go_to(goal)
            boost = (not me["in_zone"]) or not self.safe_at(pos2, t + 5, 4)
        elif snipe_step is not None:
            # snipe: long-range laser peeks from cover
            mode, move, boost = snipe_step
        elif laser_step is not None:
            # brawl: duck/jink a charging laser, then rush it during its cooldown
            mode, move, boost = laser_step
        elif duck is not None:
            # a laser is charging on us from range: break line of sight, peek after it fires
            mode = "duck"
            move = self.go_to(duck)
            boost = me["energy"] > 20
        elif commit_step is not None:
            mode, move, boost = commit_step
        elif hurt and (target is not None or t - self.last_hit_t < 2.5) and self.have_cover(t):
            mode = "hide"
            move = self.go_to(self.cover)
            boost = me["energy"] > 25
        elif not self.brawl and target is not None and target[1] and target[2] > self.engage_max() + 10 \
                and thpf > 0.35 and self.have_cover(t):
            mode = "evade"
            move = self.go_to(self.cover)
            boost = me["energy"] > 40
        elif target is not None and (target[1] or target[2] < (B_ENGAGE_D if self.brawl else self.engage_max())):
            mode, move, boost = self.fight_move(target, hpf, home)
        elif hunt_step is not None:
            # brawl, early: the lone hunter is out of sight; go find it instead of waiting
            mode, move, boost = hunt_step
        elif (t - self.last_mech_hit_t < 2.5 or (t - self.last_hit_t < 2.5 and not self.farm_ok(obs, hpf))) \
                and self.have_cover(t):
            # while farming early, drone hits don't send us running for cover
            mode = "evade"
            move = self.go_to(self.cover)
            boost = me["energy"] > 40
        else:
            mode, move = self.idle_goal(obs, hpf)
            if self.idle_jump:
                out["jump"] = True
                boost = True

        if dodge is not None:
            move = self.steer(dodge)
            boost = me["energy"] > 20

        # brawl: save energy for lightning while fighting (the zone may still force a boost)
        if self.brawl and mode in B_FIGHT_MODES and me["energy"] < B_MIN_FIGHT_ENERGY \
                and self.weapon("lightning")[1] is not None:
            boost = False

        # ----- aim / fire
        self.cur_mode = mode
        if mode == "snipe" and target is not None:
            self.snipe_shoot(target, out)
        elif target is not None:
            m, vis, dist = target
            self.shoot(self.mech_center(m), m["vel"], vis, out)
        elif self.commit_id in self.enemies:
            # committed target out of view: aim at its last known spot (mortar can still lob)
            e = self.enemies[self.commit_id]
            self.shoot(self.mech_center(e), e["vel"], False, out)
        else:
            self.idle_aim(obs, move, tb, out)
        # hit from behind with nothing in view: turn the front armor toward it now
        if target is None and self.rear_hit is not None and t - self.rear_hit[0] < 1.0:
            out["aim"] = [self.rear_hit[1], 0.0]
        if self.snipe and mode != "snipe":
            # don't dump a laser charge into a wall: hold it (it fires itself after hold_s) unless we can see
            ri, las = self.weapon("laser")
            if las is not None and (las.get("charge") or 0) > 0 and "fire" in out \
                    and not (target is not None and target[1]):
                out["fire"][ri] = True

        self.manage_loot(obs, out)

        # quiet walking when nothing is going on
        if mode in ("roam", "camp", "heal") and not boost:
            sp = self.cfg["sound"]["step_speed"] / max(1, self.stats.get("walk_speed", 11.7))
            move = [move[0] * sp * 0.92, move[1] * sp * 0.92]

        out["move"] = move
        out["boost"] = bool(boost)
        self.unstick(move, out)

        # use leftover time for path search
        spent = time.perf_counter() - tick_start
        self.plan.step(max(0.0, 0.010 - spent))
        self.mode = mode
        mf.draw.label(f"{mode}{(':' + self.sn_phase) if self.sn_phase else ''} hp{hpf:.2f} e{me['energy']:.0f}"
                      + (f" c{self.commit_id}" if self.commit_id is not None else ""))
        return out

    # ------------------------------------------------------------------ v7: snipe
    def snipe_plan(self, m):
        """Cover hidden from the target, plus the nearest spot beside it with line of sight to the target."""
        t = self.t
        tc = self.mech_center(m)
        sn = self.sn
        if (sn is not None and sn["tid"] == m["id"] and t - sn["t"] < SN_REPLAN_S and d2(sn["tpos"], tc) < 15
                and (sn["cover"] is None or self.safe_at(sn["cover"], t + 10, 5))):
            return sn
        cover = self.find_cover([tc])                       # time-guarded (8 ms)
        peek = None
        if cover is not None:
            deadline = time.perf_counter() + 0.004
            mh = self.stats.get("muzzle_height", 7)
            for r in SN_PEEK_R:
                for k in range(8):
                    if time.perf_counter() > deadline:
                        break
                    a = k * TAU / 8
                    q = (cover[0] + math.cos(a) * r, cover[1] + math.sin(a) * r)
                    if not self.w.walkable(*q) or not self.safe_at(q, t + 10, 5):
                        continue
                    if self.w.los((q[0], q[1], self.w.ground(*q) + mh), tc):
                        peek = q
                        break
                if peek is not None:
                    break
        self.sn = dict(tid=m["id"], tpos=tc, t=t, cover=cover, peek=peek)
        return self.sn

    def snipe_step(self, obs, target, hpf):
        """Target in view at 60-250 m and we hold a laser: charge in cover, peek, release, back. -> (mode, move, boost)"""
        if target is None or hpf < B_HIDE_HPF:
            return None
        ri, las = self.weapon("laser")
        if las is None:
            return None
        m, vis, dist = target
        if not (SN_MIN_D <= dist <= SN_MAX_D):
            return None
        plan = self.snipe_plan(m)
        cover, peek = plan["cover"], plan["peek"]
        if peek is None and vis:
            peek = (self.pos[0], self.pos[1])             # we can already see it from here
        if peek is None:
            return None                                   # no way to get a shot from near here
        if cover is None:
            cover = peek                                  # nowhere to hide: hold the firing spot
        full = las["stats"].get("charge", 0.75)
        ch = las.get("charge", 0) or 0
        charged = ch >= full - 0.06
        enemy_charging = any(e["kind"] == "mech" and e.get("charging") and d2(e["pos"], self.pos) > 45
                             for e in obs["mechs"])
        self.target_id = m["id"]
        if las["cooldown"] > 0.05 or (enemy_charging and not charged):
            self.sn_phase = "cover"
            goal = cover
        elif not charged:
            self.sn_phase = "charge"                      # charge behind cover (half speed is fine here)
            goal = cover
        else:
            self.sn_phase = "peek"                        # full charge: step out, release when on target
            goal = peek
        gd = d2(goal, self.pos)
        if gd > 4:
            move = self.go_to(goal)
        elif gd > 0.8:
            # go_to() calls < 4 m "arrived"; a peek spot has to be hit exactly
            move = self.steer((goal[0] - self.pos[0], goal[1] - self.pos[1]), min(1.0, gd / 3 + 0.3))
        else:
            move = [0.0, 0.0]
            if self.sn_phase == "peek" and not vis:
                plan["t"] = -99                           # at the peek spot but no line: replan next tick
        return "snipe", move, False

    def snipe_shoot(self, target, out):
        """Aim dead center (laser is hitscan); laser per snipe phase; gun only inside 100 m."""
        m, vis, dist = target
        me = self.me
        tc = self.mech_center(m)
        exact = self.aim_to(tc)
        actual = me["aim"]
        fire = [False, False]
        ri, las = self.weapon("laser")
        gi, gun = self.weapon("gun")
        los_now = self.w.los(self.muzzle(), tc)
        err = abs(wrap(actual[0] - exact[0])) + abs(actual[1] - exact[1])
        if las is not None:
            full = las["stats"].get("charge", 0.75)
            ch = las.get("charge", 0) or 0
            if ch >= full - 0.06:
                if self.sn_phase == "peek" and los_now and err < max(math.atan2(2.5 * 0.7, dist), 0.01):
                    pass                                   # let go: beam fires
                else:
                    fire[ri] = True                        # hold the full charge (up to hold_s)
            elif ch > 0:
                fire[ri] = True
            elif self.sn_phase in ("charge", "peek") and las["cooldown"] <= 0.05:
                fire[ri] = True                            # start charging
        if gun is not None and dist < SN_GUN_MAX_D and los_now:
            tol = max(math.atan2(2.0, max(dist, 1)), 0.012)
            if abs(wrap(actual[0] - exact[0])) < tol and abs(actual[1] - exact[1]) < tol * 1.5:
                fire[gi] = True
            if gun.get("ammo", 1) == 0 and gun.get("reload", 0) == 0:
                out["reload"] = True
        out["aim"] = exact
        out["fire"] = fire

    # ------------------------------------------------------------------ v4: pure survival
    def survive_spot(self):
        """Stay at the current spot until walking to the next one (+ margin) would run into the shrink."""
        t = self.t
        nxt = self.pick_spot()
        cur = self.s_spot
        if cur is None or cur == nxt:
            self.s_spot = nxt
            return nxt
        eta = d2(nxt, self.pos) * 1.3 / 4.5          # path detour, quiet walking speed
        if t + eta + S_LEAVE_MARGIN >= self.z["shrink_start"] or not self.safe_at(cur, t + eta + S_LEAVE_MARGIN, 8):
            mf.log(f"t{t:.1f} moving to next spot, eta {eta:.0f}s")
            self.s_spot = nxt
        return self.s_spot

    def look_dir(self, spot):
        """Yaw with the longest open sight line from `spot` (where hunters can see us from), cached per spot."""
        if self.s_look is not None and self.s_look[0] == spot:
            return self.s_look[1]
        nc = self.z["next_center"]
        yaw = math.atan2(nc[1] - spot[1], nc[0] - spot[0])
        gz = self.w.ground(*spot)
        eye = (spot[0], spot[1], gz + 8)
        deadline = time.perf_counter() + 0.005
        best = -1
        for k in range(8):
            a = k * TAU / 8
            open_m = 0
            for r in (40, 80, 120):
                if time.perf_counter() > deadline:
                    break
                q = (spot[0] + math.cos(a) * r, spot[1] + math.sin(a) * r)
                if not self.w.los(eye, (q[0], q[1], self.w.ground(*q) + 5)):
                    break
                open_m = r
            if open_m > best:
                best, yaw = open_m, (a if open_m > 0 else yaw)
        self.s_look = (spot, yaw)
        return yaw

    def act_survive(self, obs, out, tick_start):
        t = self.t
        me = self.me
        hpf = me["hp"] / max(1, me["max_hp"])
        pos2 = (self.pos[0], self.pos[1])
        mz = self.muzzle()
        tb = self.threat_bearing()
        zone_ok_soon = self.safe_at(pos2, t + 12, 10)

        # visible enemies, nearest first; line of sight for the nearest few only
        vis_list = []
        for m in obs["mechs"]:
            if m["kind"] == "mech" and m["id"] != self.me_id:
                vis_list.append((d2(m["pos"], self.pos), len(vis_list), m))
        vis_list.sort()
        if vis_list:
            self.last_enemy_seen_t = t
        enemies = []
        for k, (d, _, m) in enumerate(vis_list):
            los = k < 4 and self.w.los(mz, self.mech_center(m))
            hpf_m = m.get("hp", 1400) / max(1, m.get("max_hp", 1400))
            enemies.append((d, m, los, hpf_m))

        hit_recent = t - self.last_mech_hit_t < 2.5
        if hit_recent:
            self.s_hiding = True                     # once hit, regen behind cover before moving on
        attacker = None
        for e in enemies:
            if hit_recent and e[1]["id"] == self.last_dmg_by:
                attacker = e
        near = [e for e in enemies if e[0] <= S_CLOSE_D]
        finisher = None
        for e in enemies:
            if e[2] and e[3] < S_FINISH_HPF and e[0] <= S_IGNORE_D:
                finisher = e
                break
        covered = (hit_recent or near) and self.have_cover(t)

        move, boost, mode, tgt = [0.0, 0.0], False, "camp", None
        if not me["in_zone"] or not zone_ok_soon:
            mode = "zone"
            move = self.go_to(self.pick_spot())
            boost = (not me["in_zone"]) or not self.safe_at(pos2, t + 5, 4)
            tgt = attacker or (near[0] if near else None)
        elif near or finisher or (hit_recent and not covered):
            # forced to fight: shoot back, still prefer cover (unless we're finishing someone)
            mode = "fight"
            tgt = finisher or (min(near, key=lambda e: e[3] * 3 + e[0] / 35) if near else None) or attacker \
                or (enemies[0] if enemies else None)
            if covered and finisher is None:
                move = self.go_to(self.cover)
                boost = me["energy"] > 25
            elif tgt is not None:
                ax, ay = norm2(tgt[1]["pos"][0] - self.pos[0], tgt[1]["pos"][1] - self.pos[1])
                if t > self.strafe_until:
                    self.strafe_dir = self.rng.choice((-1, 1))
                    self.strafe_until = t + self.rng.uniform(0.5, 1.4)
                radial = -0.5 if tgt[0] < 20 else 0.0     # sidestep, never close in
                move = self.steer((ax * radial - ay * self.strafe_dir, ay * radial + ax * self.strafe_dir))
                nxt = (self.pos[0] + move[0] * 15, self.pos[1] + move[1] * 15)
                if not self.safe_at(nxt, t + 8, 6):
                    move = [0.0, 0.0]
        elif hit_recent:
            # being shot from range and cover exists: get out of sight, shoot back only if they're close
            mode = "evade"
            self.s_hide_spot = self.cover
            move = self.go_to(self.cover)
            boost = me["energy"] > 25
            if attacker is not None and attacker[0] <= S_IGNORE_D:
                tgt = attacker
        elif self.s_hiding and hpf < S_HEAL_DONE:
            mode = "hide"
            sp = self.s_hide_spot
            if sp is None or not self.safe_at(sp, t + 10, 5):
                sp = self.s_hide_spot = (self.cover if self.have_cover(t) else None) or self.survive_spot()
            if d2(sp, pos2) > 3:
                move = self.go_to(sp)
        else:
            if self.s_hiding:
                mf.log(f"t{t:.1f} regen done hp{hpf:.2f}")
            self.s_hiding = False
            self.s_hide_spot = None
            spot = self.survive_spot()
            mode, move = "camp", [0.0, 0.0]
            no_enemy_5s = t - self.last_enemy_seen_t > 5
            # a weapon right next to us, only when it's quiet
            for pk in obs["pickups"]:
                q = (pk["pos"][0], pk["pos"][1])
                if (no_enemy_5s and d2(q, pos2) < S_LOOT_D and self.want_pickup(pk)
                        and abs(pk["pos"][2] - self.w.ground(*q)) < 2 and self.safe_at(q, t + 15, 8)):
                    mode, move = "loot", self.go_to(q)
                    break
            if mode == "camp" and not enemies:
                for c in obs["cubes"]:
                    q = (c["pos"][0], c["pos"][1])
                    if d2(q, pos2) < S_CUBE_D and self.w.walkable(*q) and self.safe_at(q, t + 10, 6):
                        mode, move = "cube", self.go_to(q)
                        break
            if mode == "camp" and d2(spot, pos2) > 3:
                mode, move = "roam", self.go_to(spot)

        dodge = self.mortar_dodge(obs) if mode != "zone" else None
        if dodge is not None:
            move = self.steer(dodge)
            boost = me["energy"] > 20

        # ----- aim: shoot only when forced, otherwise face whoever is out there
        self.cur_mode = mode
        rear = self.rear_hit is not None and t - self.rear_hit[0] < 1.0
        if tgt is not None:
            m = tgt[1]
            self.shoot(self.mech_center(m), m["vel"], tgt[2], out)
        elif rear:
            out["aim"] = [self.rear_hit[1], 0.0]
        elif enemies:
            out["aim"] = self.aim_to(self.mech_center(enemies[0][1]))   # watch, don't shoot
        elif tb is not None:
            out["aim"] = [tb, 0.0]
        else:
            base = self.look_dir(self.s_spot or pos2)
            out["aim"] = [wrap(base + math.sin(t * 0.5 + self.scan_phase) * S_SWEEP), 0.0]
        if tgt is None:
            out["fire"] = [False, False]

        self.manage_loot(obs, out)

        # silent walking unless the zone (or incoming fire) makes us hurry
        if mode not in ("zone", "fight", "evade") and not boost:
            sp = self.cfg["sound"]["step_speed"] / max(1, self.stats.get("walk_speed", 11.7))
            move = [move[0] * sp * 0.92, move[1] * sp * 0.92]

        out["move"] = move
        out["boost"] = bool(boost)
        self.unstick(move, out)
        spent = time.perf_counter() - tick_start
        self.plan.step(max(0.0, 0.010 - spent))
        self.mode = mode
        mf.draw.label(f"S:{mode} hp{hpf:.2f} e{me['energy']:.0f}")
        return out

    # ------------------------------------------------------------------ v3: fight smarter
    def est_hp(self, eid):
        """Last seen hp, minus damage we dealt since, plus regen after regen_delay without combat."""
        e = self.enemies.get(eid)
        if e is None:
            return None
        mx = e.get("max_hp", 1400)
        hp = e.get("hp", mx)
        hp -= self.dmg_dealt.get(eid, 0) - e.get("dmg_at_seen", 0)
        start = max(e["t"], self.last_hit_on.get(eid, -99) + self.regen_delay,
                    self.last_attack_by.get(eid, -99) + self.regen_delay)
        hp += self.regen_per_s * max(0.0, self.t - start)
        return max(0.0, min(mx, hp))

    def others_near(self, obs, exclude, r=80):
        n = 0
        for m in obs["mechs"]:
            if m["kind"] == "mech" and m["id"] not in (exclude, self.me_id) and d2(m["pos"], self.pos) < r:
                n += 1
        return n

    def drop_commit(self, why):
        if self.commit_id is not None:
            mf.log(f"t{self.t:.1f} drop commit {self.commit_id}: {why}")
            self.commit_block[self.commit_id] = self.t
        self.commit_id = None

    def update_commit(self, obs):
        t = self.t
        if self.commit_id is not None and self.commit_id not in self.enemies:
            self.drop_commit("gone")
        if self.commit_id is not None:
            return
        my_hp = self.me["hp"]
        best, be = None, 1e18
        for eid, e in self.enemies.items():
            if t - self.commit_block.get(eid, -99) < COMMIT_REBLOCK:
                continue
            est = self.est_hp(eid)
            mx = e.get("max_hp", 1400)
            if est < COMMIT_HPF * mx or (t - self.last_hit_on.get(eid, -99) < 3 and est < my_hp):
                if est < be:
                    best, be = eid, est
        if best is not None and self.others_near(obs, best) < 2:
            self.commit_id = best
            mf.log(f"t{t:.1f} commit {best} est_hp {be:.0f}")

    def commit_step(self, obs, target, hpf, home):
        """(mode, move, boost) while committed to a kill, or None to fall through to normal logic."""
        cid = self.commit_id
        if cid is None:
            return None
        e = self.enemies.get(cid)
        if e is None:
            self.drop_commit("gone")
            return None
        est = self.est_hp(cid)
        if hpf < 0.25 and est > self.me["hp"]:
            self.drop_commit("losing")
            return None
        if self.others_near(obs, cid) >= 2:
            self.drop_commit("crowd")
            return None
        if target is not None and target[0]["id"] == cid and target[1]:
            mode, move, boost = self.fight_move(target, hpf, home)
            return "commit", move, boost
        # out of sight: chase its last known position so it can't sit and regen
        goal = (e["pos"][0], e["pos"][1])
        move = self.go_to(goal)
        nxt = (self.pos[0] + move[0] * 15, self.pos[1] + move[1] * 15)
        if not self.safe_at(nxt, self.t + 8, 6):
            self.drop_commit("zone")
            return None
        self.target_id = cid
        return "chase", move, self.me["energy"] > 30

    def fight_move(self, target, hpf, home):
        t = self.t
        m, vis, dist = target
        self.target_id = m["id"]
        # brawl has no ambush: an enemy the server shows us is one to close on
        committed = m["id"] == self.commit_id or self.brawl
        boost = False
        mode = "fight"
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
        if not vis and committed:
            move = self.go_to((m["pos"][0], m["pos"][1]))
            boost = self.me["energy"] > 30
        elif not vis:
            # healthy enemy out of sight: don't walk into the open, let it come to us
            mode = "ambush"
            move = [0.0, 0.0]
            if self.w.exposure(self.pos[0], self.pos[1]) > AMBUSH_EXPO and self.have_cover(t) \
                    and d2(self.cover, self.pos) < 25:
                move = self.go_to(self.cover)
        elif self.brawl and (self.snipe or self.weapon("lightning")[1] is not None or self.weapon("laser")[1] is None):
            move, boost = self.brawl_close(m, dist)      # snipe: inside 60 m it brawls too
        else:
            move = self.steer((ax * radial - ay * self.strafe_dir, ay * radial + ax * self.strafe_dir))
        # don't strafe out of the zone
        nxt = (self.pos[0] + move[0] * 15, self.pos[1] + move[1] * 15)
        if not self.safe_at(nxt, t + 8, 6):
            move = self.go_to(home or tuple(self.z["next_center"]))
        return mode, move, boost

    def duck_cover(self, obs):
        """Cover point to break LOS from a charging laser (> 40 m), or None."""
        t = self.t
        cid = self.commit_id
        if cid is not None:
            e = self.enemies.get(cid)
            est = self.est_hp(cid)
            if e is not None and est is not None and est < 0.25 * e.get("max_hp", 1400):
                return None                      # about to finish it: don't duck
        charging = False
        for m in obs["mechs"]:
            if m["kind"] == "mech" and m.get("charging") and d2(m["pos"], self.pos) > 40:
                charging = True
                break
        recent_shot = t - self.laser_fire_t < LASER_PEEK_S and t - self.charge_seen_t < 5
        if not (charging or recent_shot):
            return None
        if not self.have_cover(t) or d2(self.cover, self.pos) > DUCK_COVER_D:
            return None
        return self.cover

    def update_heal(self, hpf):
        t = self.t
        start = B_HEAL_START if self.brawl else HEAL_START
        just_killed = t - self.last_kill_t < 1.0 and (not self.brawl or hpf < start)
        if hpf >= HEAL_DONE:
            if self.healing:
                mf.log(f"t{t:.1f} healed")
            self.healing = False
            self.heal_spot = None
        elif not self.healing and (just_killed or (t - self.last_enemy_seen_t >= 3 and hpf < start)):
            self.healing = True
            self.heal_spot = None

    def heal_goal(self, obs):
        """Grab nearby cubes, then stand still somewhere low-exposure until hp is back."""
        t = self.t
        p = (self.pos[0], self.pos[1])
        best = None
        for c in obs["cubes"]:
            d = d2(c["pos"], p)
            q = (c["pos"][0], c["pos"][1])
            if d < 30 and (best is None or d < best[0]) and self.w.walkable(*q) and self.safe_at(q, t + 10, 6):
                best = (d, q)
        if best is not None:
            return "cube", self.go_to(best[1])
        sp = self.heal_spot
        if sp is None or not self.safe_at(sp, t + 15, 5):
            sp = self.cover if self.have_cover(t) else None
            if sp is None:
                sp = self.pick_spot()
            self.heal_spot = sp
        if d2(sp, p) < 3:
            return "heal", [0.0, 0.0]
        return "heal", self.go_to(sp)

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

    # ------------------------------------------------------------------ v5: brawl
    def zig(self):
        t = self.t
        if t > self.zig_until:
            self.zig_dir = -self.zig_dir
            self.zig_until = t + self.rng.uniform(*B_ZIG)
        return self.zig_dir

    def brawl_close(self, m, dist):
        """Zigzag in to preferred range, around to the target's back if that's cheap. -> (move, boost)"""
        me = self.me
        if self.weapon("lightning")[1] is not None:
            lo, hi = B_LIGHTNING_BAND
        else:
            lo, hi = B_GUN_PREF - 8, B_GUN_PREF + 8
        tx, ty = m["pos"][0], m["pos"][1]
        goal = (tx, ty)
        if dist > hi + 10 and m.get("aim"):
            # its front arc is 180 deg around its aim yaw; behind it, hits do 1.5x
            yaw = m["aim"][0]
            ang = math.atan2(self.pos[1] - ty, self.pos[0] - tx)
            if abs(wrap(ang - yaw)) < math.pi / 2:
                back = (tx - math.cos(yaw) * (lo + hi) / 2, ty - math.sin(yaw) * (lo + hi) / 2)
                extra = d2(back, self.pos) - (dist - (lo + hi) / 2)
                if extra < 40 and self.w.walkable(*back) and self.safe_at(back, self.t + 10, 6):
                    goal = back
        ax, ay = norm2(goal[0] - self.pos[0], goal[1] - self.pos[1])
        z = self.zig()
        if dist > hi:
            fwd, side = 1.0, 0.45
        elif dist < lo:
            fwd, side = -0.6, 0.8
        else:
            fwd, side = 0.0, 1.0
        move = self.steer((ax * fwd - ay * side * z, ay * fwd + ax * side * z))
        return move, dist > B_BOOST_CLOSE_D and me["energy"] > 40

    def brawl_laser(self, obs):
        """Duck or jink a charging laser at range; rush it in the cooldown after it fires. -> (mode, move, boost)"""
        t = self.t
        me = self.me
        cid = self.commit_id
        if cid is not None:
            e = self.enemies.get(cid)
            est = self.est_hp(cid)
            if e is not None and est is not None and est < 0.25 * e.get("max_hp", 1400):
                return None                      # about to finish it: keep shooting
        charger, cd = None, 1e9
        for m in obs["mechs"]:
            if m["kind"] == "mech" and m.get("charging"):
                d = d2(m["pos"], self.pos)
                if B_LASER_MIN_D < d < cd:
                    charger, cd = m, d
        if charger is not None:
            if self.have_cover(t) and d2(self.cover, self.pos) <= B_DUCK_D:
                return "duck", self.go_to(self.cover), me["energy"] > 20
            ax, ay = norm2(charger["pos"][0] - self.pos[0], charger["pos"][1] - self.pos[1])
            z = self.zig()
            move = self.steer((-ay * z, ax * z))
            nxt = (self.pos[0] + move[0] * 15, self.pos[1] + move[1] * 15)
            if self.safe_at(nxt, t + 8, 6):
                return "jink", move, me["energy"] > 25
            return None
        # punish window: it just fired, its laser is cooling down
        best, bd = None, 1e9
        for e in self.enemies.values():
            if t - e.get("laser_fire_t", -99) < B_PUNISH_S and t - e["t"] < 2:
                d = d2(e["pos"], self.pos)
                if B_LIGHTNING_BAND[1] < d < bd:
                    best, bd = e, d
        if best is None:
            return None
        ax, ay = norm2(best["pos"][0] - self.pos[0], best["pos"][1] - self.pos[1])
        z = self.zig()
        if self.w.clear_walk(self.pos, best["pos"]):
            move = self.steer((ax - ay * 0.25 * z, ay + ax * 0.25 * z))
        else:
            move = self.go_to((best["pos"][0], best["pos"][1]))
        nxt = (self.pos[0] + move[0] * 15, self.pos[1] + move[1] * 15)
        if not self.safe_at(nxt, t + 8, 6):
            return None
        return "punish", move, me["energy"] > 10

    def update_hunt(self, hpf):
        """First ~90 s: exactly one enemy seen recently -> that's the one to kill."""
        t = self.t
        recent = [eid for eid, e in self.enemies.items() if t - e["t"] < COMMIT_MEMORY]
        hid = recent[0] if t < B_HUNT_T and len(recent) == 1 else None
        if hid != self.hunt_id and hid is not None:
            mf.log(f"t{t:.1f} hunting {hid}")
        self.hunt_id = hid

    def hunt_step(self, target):
        """Hunter remembered but not in view: walk to its last known position (hp > 60%)."""
        hid = self.hunt_id
        if hid is None or self.me["hp"] / max(1, self.me["max_hp"]) <= 0.6:
            return None
        if target is not None and target[0]["id"] == hid:
            return None                              # it's in obs: the fight branch handles it
        e = self.enemies.get(hid)
        if e is None:
            return None
        goal = (e["pos"][0], e["pos"][1])
        move = self.go_to(goal)
        nxt = (self.pos[0] + move[0] * 15, self.pos[1] + move[1] * 15)
        if not self.safe_at(nxt, self.t + 8, 6):
            return None
        self.target_id = hid
        return "hunt", move, d2(goal, self.pos) > B_BOOST_CLOSE_D and self.me["energy"] > 40

    def loot_after_kill(self, obs):
        """Cubes and weapons the dead mech dropped, within 40 m. -> (mode, move) or None"""
        t = self.t
        p = (self.pos[0], self.pos[1])
        best = None
        for c in obs["cubes"]:
            q = (c["pos"][0], c["pos"][1])
            d = d2(q, p)
            if d < B_LOOT_AFTER_KILL_D and (best is None or d < best[0]) and self.w.walkable(*q) \
                    and self.safe_at(q, t + 10, 6):
                best = (d, "cube", q, None)
        for pk in obs["pickups"]:
            q = (pk["pos"][0], pk["pos"][1])
            d = d2(q, p)
            if d < B_LOOT_AFTER_KILL_D and self.want_pickup(pk) and (best is None or d < best[0]) \
                    and t - self.loot_block.get(pk["id"], -999) >= 60 and self.safe_at(q, t + 10, 6):
                best = (d, "loot", q, pk)
        if best is None:
            return None
        if best[1] == "loot":
            return "loot", self.loot_move(best[3])
        return "cube", self.go_to(best[2])

    def pref_range(self, m, hpf):
        if self.snipe and self.weapon("lightning")[1] is None:
            return B_GUN_PREF                      # inside 60 m snipe brawls with the gun
        if self.brawl:
            if self.weapon("lightning")[1] is not None:
                return sum(B_LIGHTNING_BAND) / 2
            if self.weapon("laser")[1] is not None:
                return 80
            return B_GUN_PREF
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

    def upgrade_rank(self):
        """id -> rank (lower is better): survival, held-weapon upgrades, general, the rest."""
        held = sorted({w["kind"] for w in self.me["weapons"] if w}, key=lambda k: -WEAPON_RANK.get(k, 0))
        if self.brawl2:
            # damage first: held weapons' damage/rate, then armor, max hp, then the rest
            order = [u for k in held for u in B2_UPS.get(k, [])] + ["armor", "plating"]
            rank = {}
            for u in order + UPGRADE_PREF + self.up_ids:
                rank.setdefault(u, len(rank))
            return rank
        order = list(SURVIVE_UPS if STRATEGY == "survive" else SURVIVAL_UPS)
        for k in held:
            order += WEAPON_UPS.get(k, [])
        order += GENERAL_UPS
        for k in ("lightning", "laser", "gun", "mortar"):
            if k not in held:
                order += WEAPON_UPS[k]
        rank = {}
        for u in order + self.up_ids:
            rank.setdefault(u, len(rank))
        return rank

    def early_rush(self):
        w = self.me["weapons"]
        w1 = w[1] if len(w) > 1 else None
        if self.snipe:
            return self.t < RUSH_T and self.weapon("laser")[1] is None
        if self.brawl2:
            # rush until the lightning + laser loadout is complete
            return self.t < RUSH_T and (self.weapon("lightning")[1] is None or self.weapon("laser")[1] is None)
        if self.brawl:
            # brawl keeps rushing until it holds lightning (a laser is only a stopgap)
            return self.t < RUSH_T and self.weapon("lightning")[1] is None
        return self.t < RUSH_T and not (w1 and w1["kind"] in ("lightning", "laser"))

    def farm_ok(self, obs, hpf):
        """Early, healthy, no mech shooting us and no hunter within 90 m."""
        if self.brawl2 and self.weapon("gun")[1] is None and self.weapon("lightning")[1] is None:
            return False                         # laser/mortar only: nothing that hits drones or crates
        snowball = self.brawl2 and self.t < B2_SNOWBALL_T and self.last_enemy_seen_t < 0 and hpf > 0.5
        if not snowball and (self.t >= FARM_T or hpf <= 0.6):
            return False
        if self.t - self.last_mech_hit_t < 4:
            return False
        for m in obs["mechs"]:
            if m["kind"] == "mech" and m["id"] != self.me_id and d2(m["pos"], self.pos) <= 90:
                return False
        return True

    def pick_farm(self, obs):
        """Closest safe NPC drone or crate within FARM_MAX_D, preferring ones we can see."""
        t = self.t
        mz = self.muzzle()
        cands = []
        for m in obs["mechs"]:
            if m["kind"] != "npc":
                continue
            d = d2(m["pos"], self.pos)
            if d < FARM_MAX_D and self.safe_at(m["pos"], t + 20, 8):
                c = (m["pos"][0], m["pos"][1], m["pos"][2] + 1.75)
                cands.append((d, len(cands), "npc", c, m["vel"], 1.8, m["id"], False))
        for cr in obs["crates"]:
            if t - self.crate_block.get(cr["id"], -999) < 60:
                continue
            d = d2(cr["pos"], self.pos)
            if d < FARM_MAX_D and self.safe_at(cr["pos"], t + 20, 8):
                roof = cr["pos"][2] - self.w.ground(cr["pos"][0], cr["pos"][1]) > 2
                c = (cr["pos"][0], cr["pos"][1], cr["pos"][2] + 1.2)
                cands.append((d + 10, len(cands), "crate", c, (0, 0, 0), 1.4, cr["id"], roof))
        if not cands:
            return None
        cands.sort()
        deadline = time.perf_counter() + 0.004
        first = None
        for d, _, kind, c, vel, rad, cid, roof in cands[:6]:
            vis = time.perf_counter() < deadline and self.w.los(mz, c)
            tgt = dict(kind=kind, c=c, vel=vel, radius=rad, id=cid, d=math.dist(c, mz), vis=vis)
            if vis:
                return tgt
            if first is None and not roof:      # rooftop crates only when we can already see them
                first = tgt
        return first

    def farm_move(self, ft):
        t = self.t
        tx, ty = ft["c"][0], ft["c"][1]
        d = d2((tx, ty), self.pos)
        if ft["kind"] == "crate":
            if self.farm_chase[0] != ft["id"]:
                self.farm_chase = (ft["id"], t)
            elif t - self.farm_chase[1] > 25:
                self.crate_block[ft["id"]] = t
            if d > 30 or not ft["vis"]:
                return self.go_to((tx, ty))
            return [0.0, 0.0]
        # drone: hold 35-50 m and keep strafing so its shots miss (closer when lightning is all we have)
        lo, hi = 35, 48
        if self.brawl2 and self.weapon("gun")[1] is None:
            lo, hi = B2_FARM_LIGHTNING_BAND
        if d > hi or not ft["vis"]:
            return self.go_to((tx, ty))
        ax, ay = norm2(tx - self.pos[0], ty - self.pos[1])
        if t > self.strafe_until:
            self.strafe_dir = self.rng.choice((-1, 1))
            self.strafe_until = t + self.rng.uniform(0.6, 1.5)
        radial = -0.8 if d < lo else 0.0
        move = self.steer((ax * radial - ay * self.strafe_dir * 0.7, ay * radial + ax * self.strafe_dir * 0.7))
        nxt = (self.pos[0] + move[0] * 15, self.pos[1] + move[1] * 15)
        if not self.safe_at(nxt, t + 10, 6):
            return [0.0, 0.0]
        return move

    def loot_move(self, pk):
        """Walk to a pickup; for rooftop ones, jump + boost the last stretch."""
        t = self.t
        if self.loot_chase[0] != pk["id"]:
            self.loot_chase = (pk["id"], t)
        elif t - self.loot_chase[1] > 25:
            self.loot_block[pk["id"]] = t              # can't reach it: give up for a while
        tx, ty, tz = pk["pos"]
        hd = d2((tx, ty), self.pos)
        dz = tz - self.pos[2]
        on_roof = self.pos[2] - self.w.ground(self.pos[0], self.pos[1]) > 2
        if dz > 1.0 and hd < 14:
            me = self.me
            if me["grounded"] and me["energy"] < 60:
                return [0.0, 0.0]                      # bank energy for the climb
            if me["energy"] > 5:
                self.idle_jump = True
            ux, uy = norm2(tx - self.pos[0], ty - self.pos[1])
            return [ux, uy] if hd > 1.0 else [0.0, 0.0]
        if on_roof and hd < 30:
            ux, uy = norm2(tx - self.pos[0], ty - self.pos[1])
            return [ux, uy] if hd > 1.0 else [0.0, 0.0]
        return self.go_to((tx, ty))

    def explore_target(self):
        """A not-yet-explored point inside the current zone that stays safe for a while."""
        t = self.t
        p = (self.pos[0], self.pos[1])
        g = self.explore_goal
        if g is not None and t < self.explore_until and d2(g, p) > 12 and self.safe_at(g, t + 20, 10):
            return g
        z = self.z
        c, r = z["center"], max(z["radius"] - 20, 10)
        best, bs = None, 1e18
        for _ in range(24):
            a = self.rng.random() * TAU
            rr = math.sqrt(self.rng.random()) * r
            q = (c[0] + math.cos(a) * rr, c[1] + math.sin(a) * rr)
            if not self.w.walkable(*q) or not self.safe_at(q, t + 30, 12):
                continue
            d = d2(q, p)
            if d < 40:
                continue
            seen = (int(q[0] // EXPLORE_CELL), int(q[1] // EXPLORE_CELL)) in self.seen
            sc = (500 if seen else 0) + abs(d - 90) * 0.5 + 0.2 * self.w.exposure(*q)
            if sc < bs:
                bs, best = sc, q
        self.explore_goal = best
        self.explore_until = t + 25
        return best

    def idle_goal(self, obs, hpf):
        """Nothing to fight: loot cubes/weapons, farm drones/crates, capture drops, drift to zone."""
        t = self.t
        p = (self.pos[0], self.pos[1])
        if self.brawl and t - self.last_kill_t < B_LOOT_AFTER_KILL_S:
            got = self.loot_after_kill(obs)     # the dead mech's cubes and weapons, before healing
            if got is not None:
                return got
        if self.healing:
            return self.heal_goal(obs)          # no looting far away, farming or exploring while healing
        rush = self.early_rush()
        cands = []   # (score, n, pos, kind, max_dist, dist, pickup or None, needs_walkable)
        for c in obs["cubes"]:
            d = d2(c["pos"], p)
            sc = d - 1000 if d < 40 else d - 25     # a cube within 40 m beats everything
            cands.append((sc, len(cands), (c["pos"][0], c["pos"][1]), "cube", 115, d, None, True))
        for pk in obs["pickups"]:
            if not self.want_pickup(pk) or t - self.loot_block.get(pk["id"], -999) < 60:
                continue
            q = (pk["pos"][0], pk["pos"][1])
            d = d2(q, p)
            height = pk["pos"][2] - self.w.ground(*q)
            if rush and self.brawl:
                if height > ROOF_MAX_H:
                    continue
                k = pk["kind"]
                bonus, maxd = (SN_RUSH_BONUS, SN_RUSH_MAX_D) if self.snipe else (B_RUSH_BONUS, B_RUSH_MAX_D)
                cands.append((d - bonus.get(k, 0), len(cands), q, "loot", maxd.get(k, 40), d, pk, height < 2))
            elif rush:
                if height > ROOF_MAX_H:
                    continue
                cands.append((d - RUSH_BONUS.get(pk["kind"], 0), len(cands), q, "loot", RUSH_MAX_D, d, pk, height < 2))
            elif abs(height) < 2:
                cands.append((d - 15, len(cands), q, "loot", 105, d, pk, True))
        for dr in obs.get("drops", []):
            dd = d2(dr["pos"], p)
            if dd < 180 and dr["lands_in"] < dd / 11 + 4:
                bonus = 40 if hpf < 0.7 else 0
                others = dr.get("holder") not in (None, self.me_id)
                if not others:
                    cands.append((dd - 30 - bonus, len(cands), (dr["pos"][0], dr["pos"][1]), "drop", 120 + bonus, dd, None, True))
        best = None
        for c in sorted(cands):
            sc, _, q, kind, maxd, d, pk, need_walk = c
            if d > maxd:
                continue
            if self.safe_at(q, t + d / 10 + 10, 6) and (not need_walk or self.w.walkable(*q)):
                best = c
                break
        if best:
            if best[3] == "loot":
                return "loot", self.loot_move(best[6])
            return best[3], self.go_to(best[2], 1.0)
        # early: go hunting drones and crates for cubes
        if self.farm_ok(obs, hpf):
            ft = self.pick_farm(obs)
            if ft is not None:
                self.farm_tgt = ft
                return "farm", self.farm_move(ft)
        # early with nothing to do: explore the zone instead of sitting at the camp spot
        if t < EXPLORE_T and hpf > 0.6 and t - self.last_mech_hit_t > 4:
            g = self.explore_target()
            if g is not None:
                return "explore", self.go_to(g)
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
        survive = STRATEGY == "survive"
        best, bs = None, 1e9
        for k in range(90):
            a = self.rng.random() * TAU
            r = math.sqrt(self.rng.random()) * max(nr * (0.55 if survive else 0.75), 3)
            q = (nc[0] + math.cos(a) * r, nc[1] + math.sin(a) * r)
            if not self.w.walkable(*q):
                continue
            if survive:
                # hidden first, and deep inside the next circle so the circle after it likely covers us too
                dc = d2(q, nc)
                sc = 2.0 * self.w.exposure(*q) + 0.25 * d2(q, p) + 0.5 * dc + 4.0 * max(0.0, dc - 0.4 * nr)
            else:
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
        # early farming target chosen by idle_goal: keep shooting even while the drone shoots back
        ft = self.farm_tgt
        if ft is not None:
            if ft["vis"]:
                self.shoot(ft["c"], ft["vel"], True, out, is_mech=False, radius=ft["radius"])
            else:
                out["aim"] = self.aim_to(ft["c"])
            return
        # NPC drones and crates for cubes (not while healing: dealing damage would pause our regen)
        if quiet and hpf > 0.45 and self.cur_mode != "heal":
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
        if self.cur_mode == "explore":
            sweep = math.sin(t * 1.3 + self.scan_phase) * 2.4   # wide sweep: see more of the map
        else:
            sweep = math.sin(t * 0.9 + self.scan_phase) * 1.4
        out["aim"] = [wrap(base + sweep), 0.0]


if __name__ == "__main__":
    mf.run(Bot)
