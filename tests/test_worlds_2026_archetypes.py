import json
import tempfile
import unittest
from pathlib import Path
from league_pro_archetypes import identify_archetypes, pro_archetype_signal

TANK_CARRY = {'Top':'Ornn','Jungle':'Sejuani','Mid':'Orianna','ADC':'Jinx','Support':'Lulu'}
CARRY_ENGAGE = {'Top':'Camille','Jungle':'Vi','Mid':'Ahri','ADC':"Kai'Sa",'Support':'Nautilus'}
ROLES = ('Top','Jungle','Mid','ADC','Support')

class WorldsArchetypeTests(unittest.TestCase):
    def test_classifications(self):
        self.assertIn('tank-top / protect-hypercarry', identify_archetypes(TANK_CARRY))
        self.assertIn('carry-top / engage-support', identify_archetypes(CARRY_ENGAGE))
    def test_no_worlds_no_bonus(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'pro.json'
            five=[TANK_CARRY[r] for r in ROLES]
            game={'date':'2026-10-23','winner':'A','team1':'A','team2':'B',
                  'picks':five+['Jax','Lee Sin','Ahri','Lucian','Nami']}
            path.write_text(json.dumps({'games':[dict(game,tournament='Demacia Cup Global Invitational 2026')]*5}),encoding='utf-8')
            self.assertEqual(0,pro_archetype_signal(TANK_CARRY,path=path)['adjustment'])
    def test_worlds_qualifies_after_three_distinct_games(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'pro.json'
            five=[TANK_CARRY[r] for r in ROLES]
            games=[{'date':'2026-10-23','game_id':i,'winner':'A','team1':'A','team2':'B',
                    'tournament':'Worlds 2026 Main Event',
                    'picks':five+['Jax','Lee Sin','Ahri','Lucian','Nami']} for i in range(3)]
            path.write_text(json.dumps({'games':games}),encoding='utf-8')
            signal=pro_archetype_signal(TANK_CARRY,path=path)
            self.assertGreater(signal['adjustment'],0)
            self.assertLessEqual(signal['adjustment'],.6)
            self.assertGreaterEqual(len(signal['evidence']),1)
    def test_empty_or_wrong_year_is_neutral(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'pro.json'
            path.write_text(json.dumps({'games':[{'tournament':'Worlds 2025','date':'2025-10-10',
                'winner':'A','team1':'A','team2':'B','picks':[TANK_CARRY[r] for r in ROLES]*2}]}),encoding='utf-8')
            self.assertEqual(0,pro_archetype_signal(TANK_CARRY,path=path)['adjustment'])

if __name__ == '__main__':
    unittest.main()
