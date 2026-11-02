"""Ported from Matchup Master (recur_dashboard_stats.py, 2 November 2026): own-match aggregates over Riot
match-v5 shaped rows. Pure functions, no I/O. Kept verbatim apart from relative imports."""
"""Own-match dashboard aggregates. Inputs stay private; output contains no player IDs.

Field provenance: Riot match-v5 MatchDto/ParticipantDto/TeamDto and TimelineDto,
https://developer.riotgames.com/apis#match-v5. Cache observations are not population
rankings, party membership, LP-neutral remake status, or a psychological tilt score.
"""
from calendar import monthrange
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone


FIELDS = dict(gold='goldEarned', damage='totalDamageDealtToChampions', vision='visionScore',
              wards_placed='wardsPlaced', wards_killed='wardsKilled', control_wards='detectorWardsPlaced',
              physical_damage='physicalDamageDealtToChampions', magic_damage='magicDamageDealtToChampions',
              true_damage='trueDamageDealtToChampions', damage_taken='totalDamageTaken', healing='totalHeal',
              turret_damage='damageDealtToTurrets', objective_damage='damageDealtToObjectives')
FLAGS = dict(surrender='gameEndedInSurrender', early_surrender='gameEndedInEarlySurrender',
             remake_proxy='gameEndedInEarlySurrender', first_blood_kill='firstBloodKill',
             first_blood_assist='firstBloodAssist')
MULTIS = dict(double_kills='doubleKills', triple_kills='tripleKills', quadra_kills='quadraKills', penta_kills='pentaKills')
OBJECTIVES = [('blood', 'champion', 'First blood'), ('tower', 'tower', 'First tower'),
              ('dragon', 'dragon', 'First dragon'), ('herald', 'riftHerald', 'First herald'),
              ('baron', 'baron', 'First baron'), ('inhibitor', 'inhibitor', 'First inhibitor')]
METRICS = ('kills', 'deaths', 'assists', 'kda', 'cs_per_min', 'gold_per_min', 'damage_per_min',
           'vision_per_min', 'kp', 'damage_share', 'duration', *FIELDS, *FLAGS,
           *(f'{stat}_diff_{minute}' for minute in (10, 15) for stat in ('gold', 'cs', 'xp')),
           'team_gold_diff_15')
PERCENT = {*FLAGS, 'kp', 'damage_share', 'winrate', 'share'}
NOTES = [
    'Last 20 and All use unique cached own matches across queues. Known results include early surrenders.',
    'Streaks follow cached chronology; unknown results break runs. Uncached games may interrupt a run.',
    'Sessions start at the first cached game or after at least 90 minutes from the previous game end. Cache boundaries may truncate sessions and days.',
    'Schedule uses Europe/London, including daylight saving. Games per day counts all cached games on that local date, before applying Last 20.',
    'Repeated teammates appear on your team in at least two cached matches. This premade proxy does not confirm party membership.',
    'Remake proxy uses the early-surrender flag; confirmed LP-neutral remakes are unavailable (n=0).',
    'First objectives are team ownership, not your personal last hit. Without first includes games where neither team took it. Missing flags need a complete timeline.',
    'Laning compares your unique same-role opponent on Summoner’s Rift at 10:00 or 15:00, within one second. Team gold requires all ten frames.',
    'KDA is mean per-game (kills + assists) / max(1, deaths); per-minute metrics average per-game rates. KP and damage share exclude zero team totals.',
    'No population rankings, MMR, confirmed AFK/tilt or unobserved rank history (n=0); these are not supplied by the cached matches.'
]


def observed(value, n, eligible, *, key='', reason='No observed values in this selection.'):
    return dict(value=value, n=n, missing=eligible-n, low_sample=n < 5,
                unit='percent' if key in PERCENT else 'seconds' if key == 'duration' else 'number',
                reason=reason if value is None or n < eligible else '')


def summary(rows, keys=METRICS):
    from .recur_peer_stats import number, ratio
    outcomes = [r['game']['win'] for r in rows if type(r['game']['win']) is bool]
    n, size = len(outcomes), len(rows)
    result = dict(games=observed(size, size, size), wins=observed(sum(outcomes) if n else None, n, size),
                  losses=observed(n-sum(outcomes) if n else None, n, size),
                  winrate=observed(ratio(sum(outcomes), n, 100), n, size, key='winrate', reason='No known result for some or all games.'))
    for key in keys:
        vals = [r['game'].get(key) for r in rows]
        vals = [float(v) for v in vals if type(v) is bool or number(v) is not None]
        value = sum(vals) / len(vals) * (100 if key in FLAGS else 1) if vals else None
        reason = ('Missing checkpoint, short game or no unique role opponent.' if '_diff_' in key else
                  'Field missing from some or all cached matches.' if rows else 'No games in this selection.')
        result[key] = observed(value, len(vals), size, key=key, reason=reason)
    return result


def grouped(rows, key, labels=None, *, metrics=()):
    buckets = defaultdict(list)
    for r in rows:
        value = r.get(key)
        if value is not None:
            buckets[str(value)].append(r)
    return [dict(label=label, stats=summary(buckets[label], metrics))
            for label in (labels if labels is not None else sorted(buckets))]


def london_date(timestamp):
    if timestamp is None:
        return None
    try:
        dt = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    def boundary(month):
        last = monthrange(dt.year, month)[1]
        day = last - (datetime(dt.year, month, last).weekday() + 1) % 7
        return datetime(dt.year, month, day, 1, tzinfo=timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=int(boundary(3) <= dt < boundary(10)))))


def first_objectives(info, own, events, complete):
    teams = [t for t in info.get('teams', []) if t.get('teamId') == own.get('teamId')]
    snapshot = teams[0].get('objectives', {}) if len(teams) == 1 else {}
    participants = {p.get('participantId'): p.get('teamId') for p in info.get('participants', [])}
    first = {}
    # Building teamId names the destroyed building's owner, unlike killerTeamId.
    for e in sorted(events, key=lambda e: e['timestamp']):
        key, team = None, None
        if e.get('type') == 'CHAMPION_KILL':
            key, team = 'blood', participants.get(e.get('killerId'))
            if team not in (100, 200):
                continue
        elif e.get('type') == 'BUILDING_KILL':
            key = {'TOWER_BUILDING': 'tower', 'INHIBITOR_BUILDING': 'inhibitor'}.get(e.get('buildingType'))
            team = 300 - e['teamId'] if e.get('teamId') in (100, 200) else None
        elif e.get('type') == 'ELITE_MONSTER_KILL':
            key = {'DRAGON': 'dragon', 'RIFTHERALD': 'herald', 'BARON_NASHOR': 'baron', 'BARON': 'baron'}.get(e.get('monsterType'))
            team = e.get('killerTeamId') or participants.get(e.get('killerId'))
        if key and key not in first:
            first[key] = team
    result = {}
    for key, field, _ in OBJECTIVES:
        flag = snapshot.get(field, {}).get('first')
        if type(flag) is not bool:
            flag = (first.get(key) == own.get('teamId') if key not in first or first[key] in (100, 200) else None) if complete else None
        result[key] = flag if own.get('teamId') in (100, 200) else None
    return result


def enrich_row(row, info, own, frames, events):
    """Reuse validated frames from the peer pass rather than scan disk again."""
    from .recur_peer_stats import number, total, values
    game, duration = row['game'], row['duration']
    for key, field in {**FIELDS, **MULTIS}.items():
        game[key] = number(own.get(field))
    for key, field in FLAGS.items():
        game[key] = own[field] if type(own.get(field)) is bool else None
    game['duration'] = duration
    participants = info.get('participants', [])
    frame10 = next((f for f in frames if abs(f['timestamp']-600000) <= 1000), None)
    v10 = values(own, participants, duration, frame10 if duration and duration >= 600 else None, info.get('mapId'))
    for key in ('gold', 'cs', 'xp'):
        game[key+'_diff_10'] = v10[key+'_diff_15']
    frame15 = next((f for f in frames if abs(f['timestamp']-900000) <= 1000), {})
    pf = frame15.get('participantFrames', {})
    game['team_gold_diff_15'] = None
    allies = [p for p in participants if p.get('teamId') == own.get('teamId')]
    enemies = [p for p in participants if p.get('teamId') != own.get('teamId')]
    if info.get('mapId') == 11 and len(allies) == len(enemies) == 5 and duration and duration >= 900:
        a, b = (total(pf.get(str(p.get('participantId')), {}).get('totalGold') for p in side) for side in (allies, enemies))
        game['team_gold_diff_15'] = a-b if a is not None and b is not None else None
    row.update(champion=game['champion'] or 'Unknown', role=game['role'] or 'Unknown',
               queue=str(info['queueId']) if info.get('queueId') is not None else 'Unknown',
               side={100: 'Blue', 200: 'Red'}.get(own.get('teamId')),
               objectives=first_objectives(info, own, events, row['complete']),
               ally_ids={p['puuid'] for p in allies if p.get('puuid') and p['puuid'] != own.get('puuid')},
               enemy_ids={p['puuid'] for p in enemies if p.get('puuid')},
               roster_known=len(allies) == len(enemies) == 5 and all(p.get('puuid') for p in participants),
               allied_champions={p['championName'] for p in allies if p.get('championName') and p.get('participantId') != own.get('participantId')},
               faced_champions={p['championName'] for p in enemies if p.get('championName')})


def gold_band(value):
    if value is None:
        return None
    return 'Below -2,000' if value < -2000 else '-2,000 to -1,000' if value < -1000 else '-1,000 to 0' if value < 0 else '0 to 1,000' if value < 1000 else '1,000 to 2,000' if value < 2000 else '2,000+'


def build_dashboard(rows):
    from .recur_peer_stats import ratio
    ally_counts = Counter(pid for r in rows for pid in r['ally_ids'])
    ids = sorted({pid for r in rows for pid in r['ally_ids'] | r['enemy_ids']})
    labels = {pid: f'Player {i+1}' for i, pid in enumerate(ids)}
    previous, run, result, session = None, 0, None, None
    for r in rows:
        dt = london_date(r['start'])
        gap = (r['start']-previous['start'])/60000-previous['duration']/60 if previous and r['start'] is not None and previous['start'] is not None and previous['duration'] else None
        # A missing timestamp makes chronology unobservable, not "game 1".
        session = (1 if previous is None or gap is not None and gap >= 90 else session+1 if gap is not None and gap >= 0 and session is not None else None) if dt else None
        prior = result if dt and previous and previous['start'] is not None and r['start'] > previous['start'] else None
        game = r['game']
        r.update(after_result='Win' if prior is True else 'Loss' if prior is False else None,
                 after_wins=str(run) if run < 3 else '3+' if run else None,
                 after_losses=str(run) if run < 3 else '3+' if run else None,
                 session_index=str(session) if session is not None and session < 4 else '4+' if session else None,
                 local_day=dt.date().isoformat() if dt else None,
                 local_hour=f'{dt.hour:02}:00' if dt else None,
                 weekday=('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')[dt.weekday()] if dt else None,
                 duration_band='<15 min' if r['duration'] and r['duration'] < 900 else '15–25 min' if r['duration'] and r['duration'] < 1500 else '25–35 min' if r['duration'] and r['duration'] < 2100 else '35+ min' if r['duration'] else None,
                 gold_band=gold_band(game['gold_diff_15']), team_gold_band=gold_band(game['team_gold_diff_15']),
                 premade_proxy=('Repeated teammate' if any(ally_counts[pid] >= 2 for pid in r['ally_ids']) else 'No repeated teammate') if r['roster_known'] else None)
        if prior is not True:
            r['after_wins'] = None
        if prior is not False:
            r['after_losses'] = None
        win = game['win'] if dt else None
        run = run+1 if win is not None and win is prior else 1 if win is not None else 0
        result, previous = win, r
        r['run'] = run
    days = Counter(r['local_day'] for r in rows if r['local_day'])
    for r in rows:
        r['games_per_day'] = str(days[r['local_day']]) if r['local_day'] else None
    output = {}
    for name, subset in (('all', rows), ('last20', rows[-20:])):
        size = len(subset)
        d = dict(summary=summary(subset), notes=NOTES,
                 coverage=dict(games=size, complete_timelines=sum(r['complete'] for r in subset),
                               missing_timelines=sum(not r['complete'] for r in subset),
                               known_results=sum(r['game']['win'] is not None for r in subset),
                               known_premade_proxy=sum(r['premade_proxy'] is not None for r in subset),
                               known_dates=sum(r['local_day'] is not None for r in subset)),
                 streaks=dict(current=subset[-1]['run'] or None if subset else None,
                              result=('Win' if subset[-1]['game']['win'] else 'Loss') if subset and subset[-1]['run'] else None,
                              longest_win=max((r['run'] for r in subset if r['game']['win'] is True), default=0),
                              longest_loss=max((r['run'] for r in subset if r['game']['win'] is False), default=0)))
        for key, field, group_labels in [
            ('after_result', 'after_result', ['Win', 'Loss']), ('after_wins', 'after_wins', ['1', '2', '3+']),
            ('after_losses', 'after_losses', ['1', '2', '3+']), ('by_session', 'session_index', ['1', '2', '3', '4+']),
            ('by_hour', 'local_hour', [f'{h:02}:00' for h in range(24)]),
            ('by_weekday', 'weekday', ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']),
            ('by_games_per_day', 'games_per_day', sorted({r['games_per_day'] for r in subset if r['games_per_day']}, key=int)),
            ('by_duration', 'duration_band', ['<15 min', '15–25 min', '25–35 min', '35+ min']),
            ('by_side', 'side', ['Blue', 'Red']), ('by_gold_15', 'gold_band', None), ('by_team_gold_15', 'team_gold_band', None),
            ('by_premade_proxy', 'premade_proxy', ['Repeated teammate', 'No repeated teammate'])]:
            d[key] = grouped(subset, field, group_labels)
        for key, field in [('champions', 'champion'), ('roles', 'role'), ('queues', 'queue')]:
            d[key] = sorted(grouped(subset, field, metrics=METRICS), key=lambda r: (-r['stats']['games']['value'], r['label']))
            for group in d[key]:
                group['share'] = observed(ratio(group['stats']['games']['value'], size, 100), size, size, key='share')
        d['objectives'] = []
        for key, _, label in OBJECTIVES:
            known = [r for r in subset if r['objectives'][key] is not None]
            taken = [r for r in known if r['objectives'][key]]
            d['objectives'].append(dict(key=key, label=label,
                taken=observed(ratio(len(taken), len(known), 100), len(known), size, key='share', reason='Missing team flags and no complete timeline.'),
                **{'with': summary(taken, ()), 'without': summary([r for r in known if not r['objectives'][key]], ())}))
            splits = []
            for queue, side in sorted({(r['queue'], r['side']) for r in subset if r['side']}):
                group = [r for r in subset if r['queue'] == queue and r['side'] == side]
                available = [r for r in group if r['objectives'][key] is not None]
                first = [r for r in available if r['objectives'][key]]
                splits.append(dict(queue=queue, side=side,
                    taken=observed(ratio(len(first), len(available), 100), len(available), len(group), key='share', reason='Missing team flags and no complete timeline.'),
                    **{'with': summary(first, ()), 'without': summary([r for r in available if not r['objectives'][key]], ())}))
            d['objectives'][-1]['splits'] = splits
        d['records'] = []
        for key in ('kills', 'duration', 'cs_per_min', 'damage', 'assists', 'gold', 'vision'):
            known = [r for r in subset if r['game'].get(key) is not None]
            best = max(known, key=lambda r: r['game'][key]) if known else None
            d['records'].append(dict(key=key, **observed(best['game'][key] if best else None, len(known), size, key=key),
                                     match_id=best['game']['match_id'] if best else None,
                                     champion=best['champion'] if best else None, date=best['game']['date'] if best else None))
        d['multikills'] = {}
        for key in MULTIS:
            vals = [r['game'][key] for r in subset if r['game'][key] is not None]
            d['multikills'][key] = observed(sum(vals) if vals else None, len(vals), size)
        for key, field in [('teammates', 'ally_ids'), ('opponents', 'enemy_ids'),
                           ('allied_champions', 'allied_champions'), ('faced_champions', 'faced_champions')]:
            members = defaultdict(list)
            for r in subset:
                for value in r[field]:
                    members[value].append(r)
            d[key] = sorted([dict(label=labels[value] if field.endswith('_ids') else value, stats=summary(group, ()))
                             for value, group in members.items()], key=lambda r: (-r['stats']['games']['value'], r['label']))
        output[name] = d
    return output
