"""Ported from Matchup Master (recur_peer_stats.py, 2 November 2026): own-match aggregates over Riot
match-v5 shaped rows. Pure functions, no I/O. Kept verbatim apart from relative imports."""
"""Cache-only, identity-free comparisons for Recur. No services or engine imports."""
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import mean, median
from .recur_dashboard_stats import build_dashboard, enrich_row

ROLES = {'TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY'}
METRICS = ('cs_per_min', 'gold_per_min', 'damage_per_min', 'vision_per_min',
           'kp', 'damage_share', 'deaths', 'kda', 'cs_diff_15', 'gold_diff_15', 'xp_diff_15')
BANDS = ('0-15', '15-25', '25+')


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def total(values):
    values = list(values)
    return sum(values) if values and all(number(v) is not None for v in values) else None


def ratio(a, b, scale=1):
    return a / b * scale if number(a) is not None and number(b) is not None and b > 0 else None


def average(values):
    values = [v for v in values if number(v) is not None]
    return mean(values) if values else None


def read_account(root):
    try:
        return json.loads((Path(root) / 'riot_cache/account.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}


def load_peer_cache(root):
    """Read only the inputs used here; useful for offline verification."""
    folder = Path(root) / 'riot_cache'
    return dict(account=read_account(root),
                matches=[json.loads(p.read_text(encoding='utf-8')) for p in (folder / 'matches').glob('*.json')],
                timelines={p.stem: json.loads(p.read_text(encoding='utf-8')) for p in (folder / 'timelines').glob('*.json')})


def duration_seconds(info):
    value = number(info.get('gameDuration'))
    version = str(info.get('gameVersion', '')).split('.')
    if value is not None and len(version) >= 2 and all(v.isdigit() for v in version[:2]) and tuple(map(int, version[:2])) < (11, 20):
        value /= 1000
    return value if value is not None and value > 0 else None


def opponent(player, participants, map_id):
    role, team = player.get('teamPosition'), player.get('teamId')
    allies = [p for p in participants if p.get('teamPosition') == role and p.get('teamId') == team]
    enemies = [p for p in participants if p.get('teamPosition') == role and p.get('teamId') != team]
    return enemies[0] if map_id == 11 and role in ROLES and len(allies) == len(enemies) == 1 else None


def timeline_data(match, timeline, player, duration):
    if not timeline or timeline.get('metadata', {}).get('matchId') != match['metadata']['matchId']:
        return [], [], False
    roster = timeline.get('info', {}).get('participants', [])
    if roster and not any(p.get('participantId') == player.get('participantId') and p.get('puuid') == player.get('puuid') for p in roster):
        return [], [], False
    frames = sorted((f for f in timeline.get('info', {}).get('frames', []) if number(f.get('timestamp')) is not None), key=lambda f: f['timestamp'])
    if not frames:
        return [], [], False
    events = [e for f in frames for e in f.get('events', [])]
    interval = number(timeline.get('info', {}).get('frameInterval')) or 60000
    complete = (frames[0]['timestamp'] <= 1000
                and all(0 < b['timestamp'] - a['timestamp'] <= interval + 2000 for a, b in zip(frames, frames[1:]))
                and all(isinstance(f.get('events'), list) for f in frames)
                and all(number(e.get('timestamp')) is not None for e in events)
                and (any(e.get('type') == 'GAME_END' for e in events)
                     or (duration is not None and frames[-1]['timestamp'] >= duration * 1000 - 1000)))
    return frames, events if complete else [], complete


def values(player, participants, duration, frame, map_id):
    v = dict.fromkeys(METRICS)
    minutes = duration / 60 if duration else None
    for key, fields in [('cs', ('totalMinionsKilled', 'neutralMinionsKilled')),
                        ('gold', ('goldEarned',)), ('damage', ('totalDamageDealtToChampions',)),
                        ('vision', ('visionScore',))]:
        v[key + '_per_min'] = ratio(total(player.get(f) for f in fields), minutes)
    v['deaths'] = number(player.get('deaths'))
    ka = total(player.get(f) for f in ('kills', 'assists'))
    v['kda'] = ratio(ka, max(1, v['deaths'])) if v['deaths'] is not None else None
    allies = [p for p in participants if p.get('teamId') == player.get('teamId')]
    if len(participants) == 10 and len(allies) == 5:
        v['kp'] = ratio(ka, total(p.get('kills') for p in allies), 100)
        v['damage_share'] = ratio(player.get('totalDamageDealtToChampions'), total(p.get('totalDamageDealtToChampions') for p in allies), 100)
    other = opponent(player, participants, map_id)
    if other and frame:
        pf = frame.get('participantFrames', {})
        a, b = (pf.get(str(p.get('participantId')), {}) for p in (player, other))
        for key, fields in [('cs', ('minionsKilled', 'jungleMinionsKilled')), ('gold', ('totalGold',)), ('xp', ('xp',))]:
            av, bv = (total(p.get(f) for f in fields) for p in (a, b))
            v[key + '_diff_15'] = av - bv if av is not None and bv is not None else None
    return v


def death_rows(mid, own, participants, events, map_id):
    enemies = {p.get('participantId'): p for p in participants if p.get('teamId') != own.get('teamId')}
    junglers = {pid for pid, p in enemies.items() if p.get('teamPosition') == 'JUNGLE'}
    side = {100: 'Blue', 200: 'Red'}.get(own.get('teamId'))
    rows = []
    for e in events:
        if e.get('type') != 'CHAMPION_KILL' or e.get('victimId') != own.get('participantId'):
            continue
        killer = e.get('killerId')
        assists = e.get('assistingParticipantIds')
        damage = e.get('victimDamageReceived')
        timed, untimed = set(), set()
        for d in damage or []:
            pid = d.get('participantId')
            if pid not in enemies:
                continue
            stamp = number(d.get('timestamp'))
            if stamp is None:
                untimed.add(pid)
            elif 0 <= e['timestamp'] - stamp <= 10000:
                timed.add(pid)
        # Riot omits assistingParticipantIds when nobody assisted, and victimDamageReceived is the
        # kill recap (damage taken before the death, without timestamps): a listed enemy champion
        # took part in the death. Non-champion damage carries participantId 0 and is ignored above.
        credited = {killer, *(assists or [])}
        recap = credited | timed | untimed
        involved = bool(junglers & recap) if len(junglers) == 1 else None
        solo = killer in enemies and not (recap - {killer})
        pos = e.get('position') or {}
        x, y = number(pos.get('x')), number(pos.get('y'))
        valid = map_id == 11 and side and x is not None and y is not None and 0 <= x <= 14870 and 0 <= y <= 14980
        minute = e['timestamp'] / 60000
        rows.append(dict(match_id=mid, minute=minute, x=x if valid else None, y=y if valid else None,
                         side=side, enemy_half=(x + y > 14870 if side == 'Blue' else x + y < 14870) if valid else None,
                         jungler_involved=involved, solo=solo,
                         killer_champion=enemies.get(killer, {}).get('championName'),
                         band=BANDS[0] if minute < 15 else BANDS[1] if minute < 25 else BANDS[2]))
    return rows


def death_summary(rows):
    result = dict(count=len(rows))
    for key in ('enemy_half', 'jungler_involved', 'solo'):
        known = [r[key] for r in rows if r[key] is not None]
        count = sum(known)
        result[key] = dict(count=count, share=ratio(count, len(known), 100), n=len(known), missing=len(rows)-len(known))
    return result


def session_groups(rows, field, labels, bucket):
    result = []
    for label in labels:
        group = [r for r in rows if r[field] is not None and bucket(r[field]) == label and type(r['game']['win']) is bool]
        result.append(dict(label=label, n=len(group), winrate=ratio(sum(r['game']['win'] for r in group), len(group), 100)))
    return result


def build_peer_stats(cache, *, account=None):
    """Reuse the loaded cache during rebuild; never write to the source cache."""
    puuid = (account if account is not None else cache.get('account') or {}).get('puuid')
    rows, seen = [], set()
    for match in cache.get('matches') or []:
        info, mid = match.get('info', {}), match.get('metadata', {}).get('matchId')
        participants = info.get('participants', [])
        own = [p for p in participants if puuid and p.get('puuid') == puuid]
        if len(own) != 1 or not isinstance(mid, str) or mid in seen:
            continue
        seen.add(mid)
        own = own[0]
        duration = duration_seconds(info)
        frames, events, complete = timeline_data(match, (cache.get('timelines') or {}).get(mid), own, duration)
        candidates = [f for f in frames if abs(f['timestamp'] - 900000) <= 1000]
        frame = min(candidates, key=lambda f: abs(f['timestamp'] - 900000)) if candidates and duration and duration >= 900 else None
        vals = {p['participantId']: values(p, participants, duration, frame, info.get('mapId')) for p in participants if 'participantId' in p}
        role = own.get('teamPosition') if own.get('teamPosition') in ROLES else None
        start = number(info.get('gameStartTimestamp'))
        if start is None:
            start = number(info.get('gameCreation'))
        date = datetime.fromtimestamp(start / 1000, timezone.utc).isoformat() if start is not None else None
        enemy = opponent(own, participants, info.get('mapId'))
        teammates = [p for p in participants if p.get('teamId') == own.get('teamId') and p.get('participantId') != own.get('participantId')]
        game = dict(match_id=mid, date=date, champion=own.get('championName'), role=role,
                    win=own.get('win') if type(own.get('win')) is bool else None,
                    kills=number(own.get('kills')), assists=number(own.get('assists')),
                    **vals.get(own.get('participantId'), dict.fromkeys(METRICS)),
                    team_mean={k: average(vals[p['participantId']][k] for p in teammates) if len(teammates) == 4 else None
                               for k in ('kp', 'damage_share', 'gold_per_min', 'cs_per_min')},
                    opponent_champion=enemy.get('championName') if enemy else None)
        # Any absent role makes this game's role population incomplete.
        missing_role = role is None or any(p.get('teamPosition') not in ROLES for p in participants)
        peers = [vals[p['participantId']] for p in participants if p.get('puuid') != puuid and p.get('teamPosition') == role] if not missing_role else []
        rows.append(dict(game=game, start=start, duration=duration, peers=peers, missing_role=missing_role,
                         deaths=death_rows(mid, own, participants, events, info.get('mapId')), complete=complete))
        enrich_row(rows[-1], info, own, frames, events)
    rows.sort(key=lambda r: (r['start'] is None, r['start'] or 0, r['game']['match_id']))
    previous, session, losses = None, None, None
    for r in rows:
        gap = (r['start'] - previous['start']) / 60000 - previous['duration'] / 60 if previous and r['start'] is not None and previous['start'] is not None and previous['duration'] is not None else None
        if gap is not None and gap < 0:
            gap = None
        session = None if gap is None else 1 if gap >= 90 else session + 1 if session is not None else None
        r.update(session=session, losses=losses if gap is not None else None)
        if gap is None:
            losses = None
        losses = 0 if r['game']['win'] is True else losses + 1 if r['game']['win'] is False and losses is not None else None
        # London DST without requiring the optional Windows tzdata package.
        from calendar import monthrange
        dt = datetime.fromtimestamp(r['start']/1000, timezone.utc) if r['start'] is not None else None
        if dt:
            boundaries = []
            for month in (3, 10):
                last = monthrange(dt.year, month)[1]
                day = last - (datetime(dt.year, month, last).weekday() + 1) % 7
                boundaries.append(datetime(dt.year, month, day, 1, tzinfo=timezone.utc))
            r['hour'] = (dt.hour + int(boundaries[0] <= dt < boundaries[1])) % 24
        else:
            r['hour'] = None
        previous = r
    peers, deaths = {}, dict(events=[d for r in rows for d in r['deaths']], scopes={})
    for scope, subset in (('last20', rows[-20:]), ('all', rows)):
        eligible = [r for r in subset if not r['missing_role']]
        roles = Counter(r['game']['role'] for r in subset if r['game']['role'])
        metrics = {}
        for key in METRICS:
            yours = [r['game'][key] for r in eligible if number(r['game'][key]) is not None]
            population = [p[key] for r in eligible for p in r['peers'] if number(p[key]) is not None]
            you = average(yours)
            better = sum(v > you if key == 'deaths' else v < you for v in population) if you is not None else None
            metrics[key] = dict(you=you, peers_mean=average(population), peers_median=median(population) if population else None,
                                percentile=ratio(better, len(population), 100), n_you=len(yours), n_peers=len(population), lower_is_better=key == 'deaths')
        peers[scope] = dict(metrics=metrics, roles=dict(roles), role=roles.most_common(1)[0][0] if roles else None,
                            games=len(subset), missing_role_games=len(subset)-len(eligible),
                            sessions=dict(by_session=session_groups(subset, 'session', ['1', '2', '3', '4+'], lambda n: str(n) if n < 4 else '4+'),
                                          after_losses=session_groups(subset, 'losses', ['0', '1', '2+'], lambda n: str(n) if n < 2 else '2+'),
                                          by_hour=session_groups(subset, 'hour', list(range(24)), lambda n: n)))
        ds = [d for r in subset for d in r['deaths']]
        deaths['scopes'][scope] = dict(all=death_summary(ds), bands={band: death_summary([d for d in ds if d['band'] == band]) for band in BANDS},
                                      by_role={role: death_summary([d for r in subset if r['game']['role'] == role for d in r['deaths']])
                                               for role in roles},
                                      match_ids=[r['game']['match_id'] for r in subset], complete_games=sum(r['complete'] for r in subset),
                                      missing_games=sum(not r['complete'] for r in subset))
    return dict(peers=peers, games=[r['game'] for r in rows[-20:]], deaths=deaths, dashboard=build_dashboard(rows),
                game_deaths={r['game']['match_id']: r['game']['deaths'] for r in rows})
