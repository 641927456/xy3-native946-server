"""Oracle comparisons against original 946 arithmetic, including boundary inputs."""
from pathlib import Path
import sys,unittest

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'.test-deps'))
sys.path.insert(0,str(ROOT/'analysis/attribute-native-20261125'))
from hypothesis import given,settings,strategies as st,example
from native_calc_oracle import hero_value as native_hero,guardian_value as native_guardian
from native_calc_oracle import guardian_growth as native_growth
from native946_growth import DATA,KINDS,hero_value,guardian_stats,guardian_defaults,guardian_growth


class NativeFormulaTests(unittest.TestCase):
    @settings(max_examples=400,derandomize=True,deadline=None,database=None)
    @given(attr=st.sampled_from(['cor','mag','str','spe']),race=st.sampled_from([1,2,3,7]),
           disposition=st.integers(0,19),grade=st.integers(0,200),points=st.integers(0,3000),fly=st.integers(0,2))
    @example(attr='cor',race=7,disposition=0,grade=120,points=360,fly=1)
    @example(attr='mag',race=1,disposition=0,grade=121,points=361,fly=1)
    def test_hero_formula_matches_original_bytecode(self,attr,race,disposition,grade,points,fly):
        self.assertEqual(hero_value(attr,disposition,race,grade,points,fly),
                         native_hero(DATA,attr,disposition,race,grade,points,fly))

    @settings(max_examples=400,derandomize=True,deadline=None,database=None)
    @given(kind=st.sampled_from([int(k) for k,v in DATA['summon_species'].items() if v['kind_calc'] in KINDS]),
           level=st.integers(0,185),points=st.integers(0,1500),fly=st.integers(0,2),
           rebirth=st.integers(0,2),high=st.integers(0,1),bones=st.integers(0,3))
    def test_guardian_stats_match_original_bytecode(self,kind,level,points,fly,rebirth,high,bones):
        row=dict(type=kind,level=level,cor=points,mag=points,strength=points,speed=points,
                 ascension=fly,rebirth=rebirth,high_growth=high,longgu=bones,**guardian_defaults(kind))
        actual=guardian_stats(row);bases=actual['base_values']
        native=DATA['summon_species'][str(kind)]
        state=dict(type=kind,grade=level,fly=fly,realgrow=actual['growth']/1000,
                   basehpmax=bases[0],basempmax=bases[1],baseattall=bases[2],basedodall=bases[3],
                   inihpadj=row['initial_hp_adj']/1000,inimpadj=row['initial_mp_adj']/1000,
                   iniattadj=row['initial_attack_adj']/1000,inidodadj=row['initial_speed_adj']/1000,
                   alterinihp=0,alterinimp=0,alteriniatt=0,alterinidod=0)
        for attr,key in [('cor','hpmax'),('mag','mpmax'),('str','attack'),('spe','initiative')]:
            self.assertEqual(actual[key],native_guardian(DATA,state,attr,points),(kind,level,attr))

    def test_native_level_one_vectors_and_monkey_growth_boundaries(self):
        self.assertEqual([hero_value(a,0,7,1,1) for a in ['cor','mag','str','spe']],[338,246,76,9])
        row=dict(type=3197,level=1,cor=1,mag=1,strength=1,speed=1,**guardian_defaults(3197))
        value=guardian_stats(row)
        self.assertEqual([value[k] for k in ['hpmax','mpmax','attack','initiative','growth']],[732,687,242,56,1020])
        for level,growth in [(4,1020),(5,1030),(100,1220),(140,1300),(145,1305),(185,1305)]:
            self.assertEqual(guardian_stats(dict(row,level=level))['growth'],growth)

    @settings(max_examples=400,derandomize=True,deadline=None,database=None)
    @given(kind=st.sampled_from([int(k) for k in DATA['summon_species']]),level=st.integers(0,185),
           rebirth=st.integers(0,2),fly=st.integers(0,2),high=st.integers(0,1),bones=st.integers(0,3),flight=st.integers(0,10))
    def test_guardian_growth_matches_original_arithmetic(self,kind,level,rebirth,fly,high,bones,flight):
        state=dict(type=kind,level=level,rebirth=rebirth,ascension=fly,high_growth=high,longgu=bones,
                   flight_level=flight,**guardian_defaults(kind))
        self.assertAlmostEqual(guardian_growth(state)/1000,native_growth(DATA,state),places=12)


if __name__=='__main__':unittest.main()
