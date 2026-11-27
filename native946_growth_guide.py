"""Player-facing growth guidance for connected item/activity consumers."""
def pages():
    from native946_crafting import MAKE, REFRESH, production_cash
    from native946_task_chain import RULE
    from native946_workshops import DATA

    def fees(grade):
        recipe=next(row for row in DATA['recipes'] if row['equip_grade']==grade)
        return f"升级{production_cash(recipe,MAKE):,}现金，重铸{production_cash(recipe,REFRESH):,}现金"

    second, third=RULE['quality_value_thresholds'][1:3]
    return [
      ('人物成长','通过战斗、剧情与日常任务积累经验，达到要求后自动升级，再在人物界面分配潜力。#r装备提供的根骨、灵性、力量、敏捷会参与人物实际属性计算；攻击、气血、法力、速度、防御直接影响战斗表现。#r选择装备时同时确认角色、种族、人物阶段及能力需求。'),
      ('日常修行','捉鬼：幽冥地府驹处领取，人物80级起。#r鬼王：幽冥地府钟馗处领取，需人物飞升。#r天庭：花果山马元帅处领取。#r三界令：长安宋寒处领取，完成后积累三界令积分。#r这些任务每10环一轮，人物与参战守护均可获得成长奖励；物品奖励需在背包中查看和使用。'),
      ('任务链',f"飞升{RULE['min_level']}级可在长安白居士处单人领取。每周可领取{RULE['weekly_chains']}轮，每轮{RULE['rings']}环，每环限时{RULE['ring_seconds']//60}分钟。"
       '#r拜访、交付、挑战或捕捉可推进任务。捕捉目标会直接交付。每环给经验与通宝，整轮完成额外获得现金和随机道具。'
       f'#r累计交付价值达到{second:,}或{third:,}时，整轮奖励提升一档。寻物可交一份或两份，向白居士采办只按缺额收费。'
       '#r可向白居士冻结或继续任务链，也可领取背包满时暂存的奖励。'),
      ('作坊装备','先加入作坊，再通过研习或加工积累熟练并提升段位。8段可加工16级装备，9段可加工17级装备。'
       f'#r16级需15级原装备和碧血炽玉，重铸需濯阳露。{fees(16)}，穿戴需封圣160级。'
       f'#r17级需16级原装备和千年魂石，重铸需破天石。{fees(17)}，穿戴需封圣170级。'
       '#r另需配方指定药引。刻字或带备用属性的装备暂不能加工。'),
      ('守护特技','基础特技提高守护属性。物理特技可提高躲闪、命中、致命、狂暴或反击。#r电光火石、水淹七军、灰飞烟灭和阴风怒号是主动法术，学习后可在守护战斗法术列表中选择。抗性特技降低相应人法的命中率。#r技能格随守护阶段解锁。野生守护不能学习天演图。参战可积累特技经验，也可选中一项特技进行经验转换，转换前守护需至少有500经验。'),
      ('培养方向','力量主要提高攻击，灵性主要提高法力，根骨主要提高气血，敏捷主要影响出手顺序。#r命中、躲闪、抗性和物理触发可另用对应特技、装备或修炼补足。#r守护特技经验转换最多使用当前经验的20%，接近技能上限时只扣实际所需经验。')]

def menu(chain,nid):
    return chain._menu(nid,'请选择要了解的成长途径：',[(title,dict(op='growth_page',page=i)) for i,(title,_) in enumerate(pages())])

def detail(nid,index):
    if type(index) is not int or not 0<=index<len(pages()):raise ValueError('成长指引选项无效。')
    from native946_workshops import Workshops
    title,text=pages()[index];return Workshops.tip(title,text)
