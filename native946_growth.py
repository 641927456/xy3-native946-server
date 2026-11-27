"""Revision 946 base-stat arithmetic translated from native calculators.

The JSON contains original literal tables. No formulas from other game versions
and no client-provided expressions are evaluated by the server.
"""
from pathlib import Path
from math import floor
import json

DATA=json.loads(Path(__file__).with_name('native946_growth_data.json').read_text(encoding='utf8'))
KINDS={row['kind']:row for row in DATA['summon_kinds']}


def hero_value(attribute,disposition,race,grade,points,fly=0):
    if attribute not in DATA['hero_coefficients']:raise ValueError('未知人物属性。')
    coefficients=DATA['hero_coefficients'][attribute]
    initial=DATA['hero_initial'][str(race)][attribute]
    temperament=DATA['dispositions'][str(disposition)][attribute]/1000
    racial=DATA['hero_race_fix'][str(race)][attribute]/1000
    ascended=min(fly,1)
    outer=1+ascended*coefficients[0]
    value=coefficients[1]*points*racial
    value*=1+ascended*max(grade-120,0)/100*coefficients[2]
    value*=1+ascended*coefficients[3]*max(racial-coefficients[4],0)
    value+=coefficients[5]*ascended*max((points-grade)**2-57600,0)
    return floor(initial+value*outer*temperament)


def hero_stats(row):
    disposition=row.get('disposition',0);fly=row.get('ascension',0)
    return {name:hero_value(attr,disposition,row['race'],row['level'],row[column],fly)
            for name,attr,column in [('hpmax','cor','cor'),('mpmax','mag','mag'),
                                    ('attack','str','strength'),('initiative','spe','speed')]}


def species(kind):
    row=DATA['summon_species'].get(str(kind))
    if row is None:raise ValueError('当前客户端没有这个守护种类。')
    return row


def guardian_defaults(kind,direction=None):
    native=species(kind)
    direction=native['direction'][0] if direction is None else direction
    if direction not in native['direction']:raise ValueError('这个守护不支持此培养方向。')
    return {'direction':direction,'growth_base':native['grow_max']*10,
            **{'initial_'+attr+'_adj':native[field] for attr,field in
               [('hp','hp_max'),('mp','mp_max'),('attack','att_max'),('speed','dod_max')]}}


def guardian_growth(row):
    native=species(row['type']);kind=native['kind_calc']
    level,rebirth,fly=row['level'],row.get('rebirth',0),row.get('ascension',0)
    effective_level=max(level,100 if rebirth==1 else 120 if rebirth==2 else 0,
                        140 if fly==1 else 0)
    high_factor=40 if kind==3 else 27 if kind==5 else 19
    value=min(row['growth_base']+10*(effective_level//5)+50*fly,
              native['growr_max']+high_factor*row.get('high_growth',0))
    value+=100*rebirth+10*row.get('longgu',0)+100*fly
    if kind==5:value+=30*row.get('flight_level',0)
    # Sanctification is persisted separately; growth stages use rebirth and ascension.
    return value


def hp_flight_adjustment(base,maximum):
    distance=abs(base*maximum/1000-150)
    return .15+.11*min(distance,100)/100+.11*min(max(distance-90,0),35)/35


def growth_adjustment(growth):
    return 1+min(max(floor(growth*100)-166,0)*3/100,.25)


def attack_adjustment(growth,fly):
    return 1+fly*min(max(floor(growth*100)-166,0)*.53/100,.038)


def speed_adjustment(growth,fly):
    return 1+fly*min(max(floor(growth*100)-166,0)*.51/100,.034)


def guardian_value(attr,base,adjustment,alter,points,level,growth,fly=0,maximum=1000):
    initial=adjustment*base
    if attr in ('cor','mag'):
        value=(10.5*(initial+alter)+4.5*points)*1.1*growth
        value*=1+floor(base/(40 if attr=='cor' else 45))/20
        value+=initial+alter
        value+=max(initial-base,0)*level*7/20
        if attr=='cor':value*=1+min(fly,1)*hp_flight_adjustment(base,maximum)*growth_adjustment(growth)
        else:value*=1+min(fly,1)*(.25*min(max(base*maximum/1000-49,0),1))
    elif attr=='str':
        value=(1.4*(initial+alter)+1.1*points)*1.1*growth
        value*=1+floor(base/40)/20
        value+=initial+alter
        value+=max(initial-base,0)*level/20
        value*=attack_adjustment(growth,fly)
    elif attr=='spe':
        value=(1.5*(initial+alter)+.8*points)*growth
        value*=1+floor(base/28)/20
        value+=initial+alter
        value+=max(initial-base,0)*level*.8/20
        value*=speed_adjustment(growth,fly)
    else:raise ValueError('未知守护属性。')
    return floor(value)


def guardian_stats(row):
    native=species(row['type']);base=KINDS[native['kind_calc']][str(row['direction'])]
    growth=guardian_growth(row);fly=row.get('ascension',0)
    values={}
    for i,(name,attr,column,field) in enumerate([
        ('hpmax','cor','cor','hp'),('mpmax','mag','mag','mp'),
        ('attack','str','strength','attack'),('initiative','spe','speed','speed')]):
        maximum=native['hp_max' if field=='hp' else 'mp_max' if field=='mp' else 'att_max' if field=='attack' else 'dod_max']
        values[name]=guardian_value(attr,base[i],row['initial_'+field+'_adj']/1000,
                                   row.get('alter_'+field,0),row[column],row['level'],growth/1000,fly,maximum)
    values['growth']=growth
    values['base_values']=base[:4]
    return values
