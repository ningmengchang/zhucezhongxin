"""Reviewable v4.0 engineering workbook from the 3.4 sheet and semantic snapshot.

No online document is modified. Legacy business records are retained, uncertain
relationships are explicit candidates, and every semantic path has a rule row.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from io import StringIO
import csv
import hashlib
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

ROOT = Path(__file__).resolve().parent
OUT = ROOT / '12_工程化字段表v4.0'
REF = OUT / '参考输入/工程化3.4_读取快照.json'
if not REF.exists():
    REF = Path('/tmp/dpp-engineering-3.4-reference.json')
URL = 'https://alidocs.dingtalk.com/i/nodes/m9bN7RYPWdYPgl7vCjyrayLgVZd1wyK0'
PROFILE_FILES = {
    'EV': '09_EV电池单模型/EV电池全部语义字段.schema.json',
    'LMT': '10_LMT电池单模型/LMT电池全部语义字段.schema.json',
    'IND': '11_工业电池单模型/工业电池全部语义字段.schema.json',
}
ACCESS = {'PUB': '公开', 'RES1': '合法权益人／公告机构／主管机关／欧委会',
          'RES2': '公告机构／主管机关／欧委会', 'RES3': '合法权益人·单个电池'}
PROPOSED_CODES = {
    15:'economicOperatorName',16:'economicOperatorTradeName',17:'economicOperatorTrademark',
    18:'economicOperatorPostalAddress',19:'economicOperatorWebsite',20:'economicOperatorEmail',
    21:'manufacturerName',22:'manufacturerTradeName',23:'manufacturerTrademark',
    24:'manufacturerPostalAddress',25:'manufacturerWebsite',26:'manufacturerEmail',
    34:'separateCollectionSymbol',39:'restrictedSubstanceTestReports',
    40:'recycledContentTechnicalDocuments',41:'performanceAndDurabilityTechnicalDocuments',
    42:'stateOfHealthAndLifetimeDocuments',43:'regulatoryTechnicalDocuments',44:'batteryUserManuals',
    52:'absoluteBatteryCarbonFootprint',58:'cathodeMaterialName',59:'cathodeMaterialCasNumber',
    60:'anodeMaterialName',61:'anodeMaterialCasNumber',62:'electrolyteMaterialName',
    63:'electrolyteMaterialCasNumber',64:'hazardousSubstances',65:'hazardousSubstanceCasNumber',
    66:'hazardousSubstanceClassification',67:'hazardousSubstanceLocation',
    68:'hazardousSubstanceConcentration',69:'batterySubstancesImpact',
    72:'sparePartReplacementPostalAddress',73:'sparePartReplacementWebsite',74:'sparePartReplacementEmail',
    92:'voltageRange',93:'initialPowerAt20Soc',94:'initialPowerAt80Soc',100:'remainingRoundTripEfficiency',
    105:'initialCellDcResistance',106:'initialPackDcResistance',107:'initialCellAcResistance',
    108:'initialPackAcResistance',116:'cycleLifeDischargeCRate',119:'idleStateTemperatureRange',
}
# Only explicit, reviewed concept relationships enter the direct mapping set.
DIRECT = {
    4:'$.granularity',5:'$.lastUpdate',6:'$.digitalProductPassportId',7:'$.uniqueProductIdentifier',
    10:'$.contentSpecificationIds',11:'$.batteryIdentification.modelIdentifier',
    12:'$.batteryIdentification.serialNumber',21:'$.manufacturerIdentification.manufacturerName',
    22:'$.manufacturerIdentification.manufacturerTradeName',23:'$.manufacturerIdentification.manufacturerTrademark',
    24:'$.manufacturerPostalAddress',25:'$.manufacturerContactDetails.manufacturerWebsite',
    26:'$.manufacturerContactDetails.manufacturerEmail',27:'$.manufacturingPlace',28:'$.manufacturingDate',
    30:'$.commercialWarrantyPeriod',31:'$.batteryCategory',32:'$.batteryWeight.value',33:'$.batteryStatus',
    34:'$.separateCollectionSymbol',36:'$.extinguishingAgent',
    37:'$.wasteBatteryPreventionManagementInfo.batteryLabelSymbolMeaning',38:'$.euDeclarationOfConformity',
    56:'$.batteryChemistry',57:'$.criticalRawMaterials',64:'$.hazardousSubstances',
    69:'$.wasteBatteryPreventionManagementInfo.batterySubstancesImpact',70:'$.dismantlingInformation',
    75:'$.safetyMeasures',80:'$.renewableContentShare.value',
    81:'$.wasteBatteryPreventionManagementInfo.endUserRoleInWastePrevention',
    82:'$.wasteBatteryPreventionManagementInfo.endUserRoleInSeparateCollection',
    84:'$.batteryCapacity.value',85:'$.remainingCapacity.value',86:'$.capacityFade.value',
    89:'$.stateOfCertifiedEnergy.value',91:'$.nominalVoltage.value',95:'$.remainingPowerCapability.value',
    96:'$.powerFade.value',98:'$.initialRoundTripEnergyEfficiency.value',
    99:'$.roundTripEfficiencyAtHalfCycleLife.value',100:'$.remainingRoundTripEfficiency.value',
    101:'$.roundTripEnergyEfficiencyFade.value',111:'$.expectedLifetimeYears',112:'$.expectedLifetimeCycles',
    114:'$.cycleLifeReferenceTest',115:'$.cycleLifeChargeCRate.value',116:'$.cycleLifeDischargeCRate.value',
    117:'$.capacityExhaustionThreshold.value',119:'$.notInUseTemperatureRange.valueRange',
}
CANDIDATES = {
    8:('$.registrantIdentity','经济运营商ID与注册负责主体身份未必采用同一标识体系，需确认。'),
    41:('$.complianceTestReportResults','旧项为性能和耐久性文档；当前字段为合规测试报告结果，覆盖范围需确认。'),
    42:('$.complianceTestReportResults','旧项为健康状态／寿命信息文档；不能直接等同于合规测试结果。'),
    **{i:('$.detailedComposition','旧表拆分材料名称／CAS号；当前字段为多语言详细组成文本，没有对应的独立结构子字段。') for i in range(58,64)},
    65:('$.hazardousSubstances','当前结构为物质字符串列表，没有独立CAS子字段，不能认定可无损映射。'),
    66:('$.hazardousSubstances','当前结构没有独立危险等级／类别子字段。'),
    67:('$.hazardousSubstances','当前结构没有独立位置子字段。'),
    68:('$.hazardousSubstances','当前结构没有独立浓度子字段。'),
    **{i:('$.sparePartReplacementDetails','当前字段为备件及来源资料的资源对象，没有这些独立的编号／地址／网站／邮箱子字段。') for i in range(71,75)},
    83:('$.wasteBatteryPreventionManagementInfo','一个旧指南链接与当前多个多语言业务字段不是一对一关系，需制定拆分规则。'),
    92:('$.minimalVoltage.value + $.maximumVoltage.value','旧“电压范围”口径需确认；当前电压valueRange表示温度等适用条件，不是电压上下限。'),
    93:('$.originalPowerCapability.value','旧项固定20%SoC测试条件，当前通用初始功率字段未表达该独立SoC维度。'),
    94:('$.originalPowerCapability.value','旧项固定80%SoC测试条件，不能与20%SoC值不加规则地合并。'),
    97:('$.powerLimit','旧字段为单个最大功率值，当前是可带条件范围的功率限值数组，需确认对应工况。'),
    102:('$.selfDischargingRateSet','初始单值与当前历史记录集合不同；旧单位%/月与当前%/年口径不能直接替换。'),
    103:('$.selfDischargingRateSet','当前单值与历史记录集合不同；需确认时间及%/月到%/年的业务换算规则。'),
    104:('$.selfDischargingRateSet','旧项为一个变化量，当前为自放电率数组＋记录时间；需要明确历史数据结构与口径。'),
    105:('$.internalCellResistance.value','旧项明确DC测量，当前字段未单独区分AC/DC，需要确认测试方法。'),
    106:('$.internalPackResistance.value','旧项明确DC测量，需确认测试方法及对应值。'),
    107:('$.internalCellResistance.value','旧项明确AC测量，不能与DC值直接覆盖同一个通用字段。'),
    108:('$.internalPackResistance.value','旧项明确AC测量，不能与DC值直接覆盖同一个通用字段。'),
    109:('$.internalResistanceIncrease.value','旧项指电池包层级；当前通用字段的测量层级和方法需确认。'),
    110:('$.ohmicResistance.value','旧项为当前电池包内阻；欧姆内阻的测量方法及层级需确认。'),
    113:('$.numberOfChargingCycles + $.numberOfDischargingCycles','当前分为充电和放电次数数组；不能直接认定等于一个完全等效循环次数。'),
    118:('$.environmentalConditionHistogramSet','一个温度值与温度分布直方图不同，需要明确时间窗口和统计方法。'),
    120:('$.negativeEvents','事件次数与多语言负面事件说明不同，需确认保留结构化计数及文本输出规则。'),
    121:('$.negativeEvents','事件次数与多语言负面事件说明不同，需确认统计口径及文本输出规则。'),
    122:('$.negativeEvents','事故信息只是负面事件的一部分，来源范围和结构需确认。'),
}
MAPPING_NOTES = {
    5:'保留工程编码lastUpdated；对外JSON字段为lastUpdate，使用date-time格式。',
    6:'语义Schema仅声明string；URI为旧工程要求，注册接口另有规范，不能当作本字段已有format。',
    7:'语义Schema仅声明string；注册OpenAPI另要求可读取的HTTPS UPI URL。',
    10:'不是固定版本枚举；核心、产品组、对应子类规范标识分别按语义规则计数。',
    11:'型号与批次成对，或使用序列号／产品编号／其他识别要素满足择项组合。',
    12:'序列号属于识别择项组合，不能将每个识别成员都设为无条件必填。',
    24:'制造商单一联系邮寄地址。官方类型为string，minLength=1；未设置地址专用format、pattern、maxLength或枚举，未限定文本语言。可填写中文或英文地址，不接受zh-CN/en-GB等键组成的多语言对象。3.4的“多语言=是”属于工程录入要求，不能据此改变官方传输结构；系统可另存双语版本，输出时仍提交一个地址字符串。业务上须为真实联系邮寄地址，纯空格或任意占位文字即使通过结构校验也不符合字段含义。',
    27:'由旧字符串调整为制造地点对象，需提供国家及城市／地区至少一项。',
    30:'对齐commercialWarrantyPeriod，采用非负整数月数；旧字符串需迁移规则。',
    34:'当前Schema为boolean且const=true；不是任意图片／标识枚举。',
    36:'当前为多语言文本对象，源Schema没有灭火剂的封闭枚举。',
    38:'关联资源对象须包含contentType和url，标题与language按各自规则填写。',
    64:'来源为工程化3.4第64行“有害物质名称”，原表未给机器字段名。初稿hazardousSubstanceNames是本稿拟定的工程名，现统一采用官方语义名称hazardousSubstances。官方为有害物质自由文本字符串数组（汞、镉、铅除外），不是多语言对象；CAS、浓度、位置等旧工程分项另行保留待确认。',
    84:'本行对应公开batteryCapacity；RES3另有ratedCapacity。二者同源复用及状态变化后的更新策略需确认。',
    85:'按本语义版本：EV不填写／不展示；LMT结构必填；工业为固定式储能且有BMS等条件适用。',
    89:'保留工程编码stateOfCertifiedEnergySOCE；对外路径stateOfCertifiedEnergy.value。',
    99:'保留旧工程编码，对外使用roundTripEfficiencyAtHalfCycleLife。',
    101:'保留旧工程编码，对外使用roundTripEnergyEfficiencyFade.value。',
    111:'采用整数年；词表说明状态变化时可能需更新，不能只根据“预期”一词判断永不变化。',
    115:'工程量纲A/Ah对应h⁻¹；对外unit提交reciprocalHour的完整URI。',
    116:'当前词表明确Optional；不能沿用旧表无条件M。',
    119:'调整为范围对象；旧表仅显示下限名称，需确认上界、观测特征和单位如何补齐。',
}
ZH_CODES = {
    'validStatus':'有效','invalidStatus':'无效','item':'单件','batch':'批次','model':'型号',
    'original':'原始电池','remanufactured':'再制造','repurposed':'改变用途','reused':'再使用','waste':'废电池',
    'electricVehicleBattery':'电动汽车电池','lightMeansOfTransportBattery':'轻型交通工具电池','industrialBattery':'工业电池',
    'cadmiumSymbol':'镉标识','leadSymbol':'铅标识','noneSymbol':'无相应化学符号',
    'kilogram':'千克（kg）','gram':'克（g）','ampereHour':'安时（Ah）','milliampereHour':'毫安时（mAh）',
    'percent':'百分比（%）','volt':'伏特（V）','degreeCelsius':'摄氏度（°C）','watt':'瓦（W）',
    'ohm':'欧姆（Ω）','reciprocalHour':'每小时（h⁻¹）','hour':'小时（h）','temperature':'温度',
    'antimony':'锑','arsenic':'砷','baryte':'重晶石','bauxiteAluminaAluminium':'铝土矿／氧化铝／铝',
    'beryllium':'铍','bismuth':'铋','boron':'硼','cobalt':'钴','cokingCoal':'焦煤','copper':'铜',
    'feldspar':'长石','fluorspar':'萤石','gallium':'镓','germanium':'锗','graphite':'石墨','hafnium':'铪',
    'heavyRareEarthElements':'重稀土元素','helium':'氦','lightRareEarthElements':'轻稀土元素','lithium':'锂',
    'magnesium':'镁','manganese':'锰','nickelBatteryGrade':'电池级镍','niobium':'铌','phosphateRock':'磷矿石',
    'phosphorus':'磷','platinumGroupMetals':'铂族金属','scandium':'钪','siliconMetal':'金属硅',
    'strontium':'锶','tantalum':'钽','titaniumMetal':'金属钛','tungsten':'钨','vanadium':'钒',
    'leadAcid':'铅酸电池','lithiumIonLCO':'锂离子·钴酸锂（LCO）','lithiumIonLFP':'锂离子·磷酸铁锂（LFP）',
    'lithiumIonLMO':'锂离子·锰酸锂（LMO）','lithiumIonNCA':'锂离子·镍钴铝（NCA）',
    'lithiumIonNMC':'锂离子·镍锰钴（NMC）','lithiumMetal':'锂金属电池','nickelCadmium':'镍镉电池',
    'nickelMetalHydride':'镍氢电池','sodiumIon':'钠离子电池',
}
GENERIC = {'value','unit','valueRange','observedCharacteristic','lowerBound','upperBound','bin','binValue','binRangeUnit','binValueUnit'}
DYNAMIC_ROOTS = {'lastUpdate','dppStatus','batteryStatus','capacityFade','powerFade','internalResistance',
                 'internalResistanceIncrease','roundTripEnergyEfficiency','roundTripEnergyEfficiencyFade',
                 'numberOfChargingCycles','numberOfDischargingCycles','negativeEvents',
                 'environmentalConditionHistogramSet','stateOfChargeHistogram','remainingCapacity',
                 'remainingPowerCapability','remainingRoundTripEfficiency','selfDischargingRateSet',
                 'ohmicResistance','stateOfCertifiedEnergy'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean(text):
    return str(text or '').replace('\\r\\n','\n').replace('\\r','\n').replace('\\n','\n').replace('\r\n','\n').replace('\r','\n').strip()


def first(text):
    return next((line.strip() for line in clean(text).splitlines() if line.strip()), '')


def zh_first(text):
    text = first(text)
    if text.startswith('DPP'):
        return 'DPP头信息'
    match = re.match(r'[^A-Za-z]+', text)
    return match.group().strip() if match else text


def section(text, heading):
    match = re.search(re.escape(heading) + r'：\n(.*?)(?=\n\n|$)', text or '', re.S)
    return match.group(1).strip() if match else ''


def join_unique(values, sep='\n'):
    return sep.join(dict.fromkeys(str(v) for v in values if v is not None and str(v).strip()))


def root_name(path):
    return path.split('.')[1].replace('[]','')


def field_name(path):
    return path.rsplit('.',1)[-1].replace('[]','')


def legacy_required(text, group):
    text = clean(text)
    if text in {'M','C','V'}:
        return text
    for code, categories in re.findall(r'([MCV])\(([^)]+)\)', text):
        if group in categories.split('/'):
            return code
    return '待确认'


def groups_from(text):
    return [g for g in ['EV','LMT','IND'] if g in text]


def old_format(text):
    text = clean(text)
    lower = text.lower()
    if lower == 'int': return 'integer'
    if 'decimal' in lower: return 'object（范围）' if '范围' in text else 'number'
    if text == '多 Link': return 'array<string / uri>'
    if lower in {'link','uri','image'}: return 'string / uri（工程建议）'
    if text in {'enum','枚举值'}: return 'string（枚举）'
    return text or '待确认'


def old_access(text):
    text = clean(text)
    if text.lower() == 'public': return 'PUB'
    if text == 'NB/市场监管机构/委员会': return 'RES2'
    return '待确认'


class Source:
    def __init__(self):
        obj = json.loads(REF.read_text())
        assert obj['success'] and not obj['hasMore'] and not obj['truncationReasons']
        raw = list(csv.reader(StringIO(re.sub(r'(?m)^\[row=\d+\]\s*','',obj['csv']))))
        self.legacy = []
        major = minor = ''
        for number, values in enumerate(raw[1:],2):
            if not any(v.strip() for v in values): continue
            values = [clean(v) for v in values]
            major = zh_first(values[0]) or major
            minor = zh_first(values[1]) or minor
            lines = [line.strip() for line in values[3].splitlines() if line.strip() and line.strip() != 'field']
            old_code = next((x for x in lines if re.fullmatch(r'[a-z][A-Za-z0-9]*', x)), '')
            code = PROPOSED_CODES.get(number,old_code)
            if number == 3: code = 'internalDppStatus'
            assert code and re.fullmatch(r'[a-z][A-Za-z0-9]*',code), (number,code)
            self.legacy.append(dict(row=number,raw=values,major=major,minor=minor,
                                    title=lines[0] if lines else first(values[2]),code=code,old_code=old_code))
        assert len(self.legacy) == 121
        self.models = {g:json.loads((ROOT/name).read_text()) for g,name in PROFILE_FILES.items()}
        self.index = {}
        self.catalog = defaultdict(set)
        for group, model in self.models.items():
            self.walk(group,model,'$',[],None)
            for name,node in model['properties'].items():
                self.business_catalog(group,node,'$.'+name)
        with (ROOT/'08_Apifox语义模型/枚举项明细.csv').open(encoding='utf-8-sig',newline='') as file:
            vocab = list(csv.DictReader(file))
        self.vocab = {row['完整概念URI']:row for row in vocab}

    def walk(self, group, node, path, chain, parent):
        if path != '$':
            self.index[group,path] = dict(group=group,path=path,node=node,parent=parent,chain=chain)
        for name,child in node.get('properties',{}).items():
            flags = dict(path=path+'.'+name,required=name in node.get('required',[]),
                         conditional=any(name in b.get('required',[]) for kind in ['anyOf','oneOf'] for b in node.get(kind,[]))
                         or name in node.get('dependentRequired',{})
                         or any(name in names for names in node.get('dependentRequired',{}).values()))
            self.walk(group,child,path+'.'+name,[*chain,flags],node)
        if isinstance(node.get('items'),dict):
            self.walk(group,node['items'],path+'[]',chain,node)

    def business_catalog(self, group, node, path):
        props = node.get('properties',{})
        if {'unit','value'} <= set(props) <= {'unit','value','valueRange'}:
            self.catalog[group].add(path+'.value')
            if 'valueRange' in props: self.catalog[group].add(path+'.valueRange')
            return
        self.catalog[group].add(path)
        if node.get('type') == 'array':
            items = node.get('items',{})
            if 'valueRange' in items.get('properties',{}):
                self.catalog[group].add(path+'[].valueRange')
            return
        common = node.get('x-source-locations',[])
        if any(any('/$defs/'+key in loc for key in ['multiLanguage','relatedResource','valueRange','histogram']) for loc in common):
            return
        for name,child in props.items():
            self.business_catalog(group,child,path+'.'+name)

    def context(self, entry):
        path = entry['path']
        while field_name(path) in GENERIC and path.count('.')>1:
            path = path.rsplit('.',1)[0]
            if path.endswith('[]') and (entry['group'],path) not in self.index:
                path=path[:-2]
        return self.index.get((entry['group'],path),entry)


def meaning(entry, source):
    context = source.context(entry)
    return context['node']


def format_of(node):
    typ = node.get('type','未限定')
    if typ == 'array':
        typ += '<'+node.get('items',{}).get('type','未限定')+'>'
    if typ == 'object' and 'propertyNames' in node: typ += '（多语言）'
    if node.get('format'): typ += ' / '+node['format']
    return typ


def required_of(entry, source):
    node = meaning(entry,source)
    business = section(node.get('description',''),'业务适用条件')
    note = entry['node'].get('x-semantic-required-note','')
    chain=entry['chain']
    if any(flag['conditional'] for flag in chain): return 'C', business or '按父对象组合／依赖条件填写。'
    if chain and all(flag['required'] for flag in chain): return 'M', business or '父对象及当前字段结构必填。'
    # The engineering row represents the measurement as a business field.
    # Its required "value" member does not turn an optional measurement into C.
    if business.startswith('可选') or '可选对象' in note: return 'V',business
    if business.startswith('有资料') or business.startswith('有相应资料'): return 'V',business
    if any(not flag['required'] for flag in chain[:-1]):
        return 'C',join_unique([business,'仅在对应父对象／数组元素存在时执行子字段约束。'])
    if any(word in note for word in ['条件','适用','组合','择项']): return 'C',business or note
    if business and not business.startswith('官方词表未') and any(word in business for word in ['仅','适用','开始使用','至少']):
        return 'C',business
    if note.startswith('父对象存在时可选'):
        return 'C','提供此可选子结构时，按其内部约束填写；父级规则另见规则页。'
    return '待确认',join_unique([business,'源Schema未列独立required；不直接认定为业务自愿项。'])


def dynamics(entry, source):
    node = meaning(entry,source)
    scope = section(node.get('description',''),'官方适用说明原文')
    if 'dynamic dimension' in scope.lower():
        return 'Dynamic','官方语义依据：battery-vocabulary.ttl的skos:scopeNote明确说明动态维度；电池状态变化时，该参数可能需要更新。未规定固定采集频率。'
    if root_name(entry['path']) in DYNAMIC_ROOTS:
        return 'Dynamic','工程建议：使用状态／计数／性能结果或事件变化时更新；实际频率待业务确认。'
    return 'Static','工程建议：建档／定型时确定，资料或版本变化时维护；不是禁止修改。'


def get_example(model,path):
    value=(model.get('examples') or [{}])[0]
    try:
        for part in path[2:].split('.'):
            array=part.endswith('[]'); key=part[:-2] if array else part
            value=value[key]
            if array:value=value[0]
        return json.dumps(value,ensure_ascii=False,separators=(',',':'))
    except (KeyError,IndexError,TypeError):return '官方全量样例未提供此路径值'


def enum_values(node):
    if 'enum' in node:return node['enum']
    if 'const' in node:return [node['const']]
    items=node.get('items',{})
    if 'enum' in items:return items['enum']
    return [items['const']] if 'const' in items else []


def rule_signature(node, context):
    """Class rows can merge only when actual validation and SHACL rules agree."""
    annotations={'title','description','examples','example','$comment'}
    def constraints(value):
        if isinstance(value,dict):
            return {k:constraints(v) for k,v in value.items() if k not in annotations and not k.startswith('x-')}
        if isinstance(value,list):return [constraints(v) for v in value]
        return value
    semantic=sorted({(r.get('path',''),r['predicate'],r['value'],r.get('branch',''))
                     for obj in [node,context] for r in obj.get('x-shacl-rules',[])})
    payload={'node':constraints(node),'context':constraints(context),'semantic':semantic}
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def dict_id(uri):
    return 'EU-'+uri.split('#')[0].rsplit('/',1)[-1].upper()


class Builder:
    def __init__(self, source):
        self.source=source
        self.records=[]
        self.covered=set()
        self.used_codes={row['code'] for row in source.legacy}
        self.unit_usage=defaultdict(set)
        self.enum_usage=defaultdict(set)
        self.rule_ids={}
        self.by_legacy=defaultdict(list)

    def enums(self,node,path):
        values=enum_values(node)
        if not values:return '无',None
        lines=[]; ids=[]
        for value in values:
            if isinstance(value,str) and '#' in value:
                code=value.rsplit('#',1)[-1]; group=dict_id(value)
                label=ZH_CODES.get(code,self.source.vocab.get(value,{}).get('官方名称','待翻译'))
                self.enum_usage[value].add(path)
            else:
                code=json.dumps(value,ensure_ascii=False); group='CONST-TRUE' if value is True else 'CONST-'+code
                label='必须设置为true' if value is True else '固定值'
            lines.append(code+' — '+label);ids.append(group)
        return join_unique([join_unique(ids,' / '),*lines]),ids[0]

    def mapped(self, legacy, group, path, added=False):
        source=self.source
        entry=source.index[group,path]
        node=entry['node'];context=meaning(entry,source)
        required,condition=required_of(entry,source)
        dynamic,dynamic_note=dynamics(entry,source)
        text=node.get('description','');ctext=context.get('description','')
        unit=section(text,'单位／口径') or section(ctext,'单位／口径')
        if unit.startswith('不适用') or unit.startswith('组合／列表'):unit='—（非计量字段）'
        unit=unit or '—（源未声明单位）'
        unit=unit.replace('h⁻¹','h^-1')
        enum, enum_id=self.enums(node,path)
        if 'unit' in (entry.get('parent') or {}).get('properties',{}):
            unit_node=entry['parent']['properties']['unit']
            for value in unit_node.get('enum',[]):self.unit_usage[value].add(path)
        view=node.get('x-access-view',context.get('x-access-view','PUB'))
        code=legacy['code'];title=legacy['title']
        old=legacy.get('raw')
        status='新增语义字段' if added else '已映射'
        note=MAPPING_NOTES.get(legacy.get('row'),'')
        if old and old_format(old[7]).split(' / ')[0] != format_of(node).split(' / ')[0]:
            status='结构／类型调整'
        if added and node.get('type')=='object' and node.get('properties') and not {'unit','value'}<=set(node['properties']):
            status='新增结构组' if not any('/$defs/' in loc for loc in node.get('x-source-locations',[])) else status
        if added and root_name(path)=='ratedCapacity':
            note='RES3额定容量与公开batteryCapacity可能同源；同源复用及状态变化后的更新策略待确认。'
        chinese=join_unique([
            title+'。',condition,
            section(text,'填写格式') or section(ctext,'填写格式'),
            '单位口径：'+unit if not unit.startswith('—') else '',
            note,
        ])
        if not chinese:chinese='按字段类型、父结构及源规则填写。'
        old_dynamic=old[13] if old else ''
        remarks=[note,dynamic_note]
        if old_dynamic and old_dynamic!=dynamic:
            authority='，依据官方词表的动态维度说明；具体更新频率待工程确认。' if 'dynamic dimension' in section(ctext,'官方适用说明原文').lower() else '，属工程建议，待业务确认。'
            remarks.append('动静态从旧表'+old_dynamic+'调整为'+dynamic+authority)
        if old and old[7].lower().startswith('decimal'):remarks.append('旧存储精度建议：'+old[7]+'；不是官方JSON Schema精度约束。')
        if node.get('type')=='number' and '%' in unit and 'maximum' not in node:
            remarks.append('源Schema未设maximum，不能仅凭%单位认定已限制0～100。')
        scope=section(ctext,'官方适用说明原文')
        if 'where possible' in section(ctext,'官方英文定义').lower():
            remarks.append('原文含where possible，具体可提供性需业务判断。')
        if '多语言' in format_of(node):remarks.append('动态语言键如en-IE；JSON Schema未限定逐值类型，语义层要求文本。')
        owner=old[17] if old and old[17] else '待分配'
        record=dict(code=code,title=title,major=legacy['major'],minor=legacy['minor'],groups=[group],required=required,
                    condition=condition,access=view,fmt=format_of(node),unit=unit,enum=enum,enum_id=enum_id,
                    dynamic=dynamic,english=section(ctext,'官方英文定义') or (old[16] if old else ''),
                    chinese=chinese,path=path,status=status,collection=({'0':'手动填报','2':'系统获取'}.get(old[11],old[11]) if old else '待确认'),
                    data_source='待确认（适用条件含BMS，采集接口未指定）' if 'BMS' in condition else '待确认',
                    multilingual='是' if 'propertyNames' in node else (old[12] if old else '否'),
                    example=get_example(source.models[group],path),owner=owner,remarks=join_unique(remarks),
                    oldrow=legacy.get('row'),old=old,entry=entry,added=added,
                    rule_signature=rule_signature(node,context))
        if legacy.get('row')==64:record['multilingual']='否（非多语言对象；array<string>）'
        if legacy.get('row')==24:record['multilingual']='无官方多语言结构；工程双语另行管理'
        self.covered.add((group,path))
        return record

    def unmapped(self, legacy, group):
        raw=legacy['raw'];number=legacy['row']
        candidate,reason=CANDIDATES.get(number,('','当前语义包未找到可直接确认的对应字段；保留3.4业务项，待补充来源或工程映射。'))
        if number==3:
            reason='3.4五种业务状态与当前语义包两种有效性状态含义不同；建议用internalDppStatus保留旧状态，官方dppStatus独立补齐。名称与业务状态公开范围待确认。'
        required=legacy_required(raw[5],group)
        permission=old_access(raw[6])
        if number==3:permission='待确认'
        enum=raw[10] if raw[10] and raw[10]!='/' else ('待确认（旧表未给出枚举值）' if raw[7] in ['enum','枚举值'] else '无（旧表未列枚举）')
        return dict(code=legacy['code'],title=legacy['title'],major=legacy['major'],minor=legacy['minor'],groups=[group],
                    required=required,condition='沿用3.4业务类别；当前语义包尚未确认该工程项的适用条件。',
                    access=permission,fmt=old_format(raw[7]),unit=raw[9] if raw[9] not in ['', '/'] else '—（旧表未列单位）',
                    enum=enum,enum_id='LEGACY-'+str(number).zfill(3) if enum not in ['无（旧表未列枚举）','待确认（旧表未给出枚举值）'] else None,
                    dynamic=raw[13] or '待确认',english=raw[16] or '旧表未提供英文描述；待核对。',
                    chinese=join_unique(['【3.4业务保留，待语义核对】'+(raw[15] or legacy['title']),reason]),
                    path=('候选：'+candidate) if candidate else '待映射',status='候选映射待确认' if candidate else '旧表保留待确认',
                    collection={'0':'手动填报','2':'系统获取'}.get(raw[11],raw[11]) or '待确认',data_source='待确认',
                    multilingual=raw[12] or '待确认',example=raw[14] if raw[14] not in ['', '/'] else '待补充',
                    owner=raw[17] or '待分配',remarks=join_unique([reason,'本行M/C/V、格式、单位和动静态沿用3.4，尚未由当前语义包确认。',
                    '旧披露权限：'+raw[6], '原电池分类：'+raw[4]]),oldrow=number,old=raw,entry=None,added=False)

    def build_records(self):
        for legacy in self.source.legacy:
            path=DIRECT.get(legacy['row'])
            for group in groups_from(legacy['raw'][4]):
                if path and (group,path) in self.source.index:
                    rec=self.mapped(legacy,group,path)
                elif path:
                    rec=self.unmapped(legacy,group)
                    rec.update(required='N/A',condition='该版本类别语义Schema未定义此路径；相关不适用边界按源视图核对。',
                               path=path,status='类别不适用',access='N/A',fmt='—',unit='—',enum='—',enum_id=None,
                               chinese='旧表包含该类别；本语义版本对该字段不填写／不展示，未纳入该类别模型。',
                               remarks='保留旧条目以显示类别差异；不向该类别的语义输出填入此字段。')
                else:rec=self.unmapped(legacy,group)
                self.records.append(rec)
        for path in sorted(set().union(*self.source.catalog.values())):
            available=[g for g in ['EV','LMT','IND'] if path in self.source.catalog[g] and (g,path) not in self.covered]
            if not available:continue
            sample=self.source.index[available[0],path]
            node=sample['node'];root=root_name(path)
            base=field_name(path)
            if base=='value':base=field_name(path.rsplit('.',1)[0])
            elif base=='valueRange':base=root+'ValueRange'
            code=base
            if code in self.used_codes:
                code='stateOfHealthRatedCapacity' if root=='ratedCapacity' and base=='ratedCapacity' else 'semantic'+base[0].upper()+base[1:]
            assert re.fullmatch(r'[a-z][A-Za-z0-9]*',code),code
            self.used_codes.add(code)
            context=meaning(sample,self.source)
            title=node.get('title',base)
            if field_name(path)=='value':title=context.get('title',base)
            if field_name(path)=='valueRange':title=context.get('title',root)+'·适用条件范围'
            if root=='ratedCapacity':title='健康状态视图·额定容量'
            if root in ['digitalProductPassportId','uniqueProductIdentifier','granularity','dppStatus','lastUpdate','contentSpecificationIds','registrantIdentity']:
                major,minor='DPP头信息','护照与注册主体'
            elif root in ['batteryChemistry','criticalRawMaterials','hazardousSubstances','detailedComposition']:
                major,minor='电池材料组成','化学体系与物质信息'
            elif root in ['wasteBatteryPreventionManagementInfo','sparePartReplacementDetails','dismantlingInformation','safetyMeasures']:
                major,minor='循环和资源效率','回收、拆解与资源信息'
            elif root in ['complianceTestReportResults','euDeclarationOfConformity','separateCollectionSymbol','chemicalSymbol']:
                major,minor='符号，标签和文档符合性','标识与符合性资料'
            elif root.startswith('manufacturer') or root in ['batteryIdentification','manufacturingPlace','manufacturingDate']:
                major,minor='标识符和产品数据','制造商与生产信息'
            else:major,minor='性能和耐用性','语义包补齐指标'
            legacy=dict(code=code,title=title,major=major,minor=minor)
            for group in available:self.records.append(self.mapped(legacy,group,path,added=True))
        # Merge only records whose actual business/structure rules are identical.
        merged={}
        ignore={'groups','entry','example'}
        for rec in self.records:
            signature=json.dumps({k:v for k,v in rec.items() if k not in ignore},ensure_ascii=False,sort_keys=True)
            if signature in merged:merged[signature]['groups']+=rec['groups']
            else:merged[signature]=deepcopy(rec)
        self.records=list(merged.values())
        for index,rec in enumerate(self.records,2):
            rec['excel_row']=index;rec['id']='V4-'+str(index-1).zfill(4)
            if rec['oldrow']:self.by_legacy[rec['oldrow']].append(rec)
        assert set(self.by_legacy)=={row['row'] for row in self.source.legacy}
        assert all((g,path) in self.covered for g,paths in self.source.catalog.items() for path in paths)


MAIN_HEADERS=['字段ID','数据大类','数据小类','数据项名称','工程化字段名称','电池分类','是否必填','适用条件','披露权限',
              '数据格式','单位','枚举项','动静态','英文描述','中文描述','官方JSON路径','核对状态','采集方式','采集来源',
              '多语言','示例','责任部门','工程与核对备注']
MAIN_WIDTHS=[12,20,25,28,37,17,13,50,34,27,33,45,16,68,76,65,26,18,30,12,48,18,78]
RULE_HEADERS=['记录类型','规则ID','工程字段／语义节点','电池分类','官方JSON路径','JSON类型','Schema required',
              '业务／组合／存在条件','数量／格式／结构约束','单位与枚举依据','官方英文定义／说明','来源与版本','3.4源行','主表定位']
RULE_WIDTHS=[18,15,37,13,65,24,25,86,90,82,110,110,13,18]
ENUM_HEADERS=['字典编号','来源类型','代码','中文名称','英文名称','实际提交值／完整URI','适用路径或旧条目',
              '字段限定／使用说明','官方英文定义／原文','来源依据']
ENUM_WIDTHS=[38,20,34,35,55,106,85,80,120,100]
DIFF_HEADERS=['3.4源行','v3.4工程字段原文','v4工程化字段','v4主表定位','处理类型','旧电池分类','v4电池分类',
              '旧必填','v4必填','旧披露权限','v4披露权限','旧数据格式','v4数据格式','旧单位','v4单位',
              '旧枚举项','v4枚举项','旧动静态','v4动静态','官方路径／候选路径','变更说明与待确认项']
DIFF_WIDTHS=[12,42,38,25,31,30,24,26,30,32,45,30,40,28,45,65,70,20,30,80,115]


def make_sheet(wb,name,headers,rows,widths,table_name,freeze):
    ws=wb.create_sheet(name)
    ws.append(headers)
    for values in rows:
        assert len(values)==len(headers),(name,len(values))
        ws.append(values)
    ws.freeze_panes=freeze
    ws.sheet_view.showGridLines=False
    ws.sheet_view.zoomScale=80
    ws.sheet_properties.pageSetUpPr.fitToPage=True
    ws.page_setup.orientation='landscape';ws.page_setup.paperSize=ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth=1;ws.page_setup.fitToHeight=0
    ws.print_title_rows='1:1'
    ws.print_options.horizontalCentered=False
    ws.oddFooter.center.text='工程化字段表 v4.0 · 评审稿 · &P / &N'
    ws.sheet_properties.tabColor='16384D' if name.startswith('工程化') else '2A7D77'
    for index,width in enumerate(widths,1):ws.column_dimensions[get_column_letter(index)].width=width
    for cell in ws[1]:
        cell.font=Font(name='Noto Sans CJK SC',size=11,bold=True,color='FFFFFF')
        cell.fill=PatternFill('solid',fgColor='173A52')
        cell.alignment=Alignment(vertical='center',wrap_text=True)
        if any(k in str(cell.value) for k in ['必填','条件']):cell.fill=PatternFill('solid',fgColor='85612A')
        elif any(k in str(cell.value) for k in ['单位','枚举','格式']):cell.fill=PatternFill('solid',fgColor='246D70')
    ws.row_dimensions[1].height=42
    for cells in ws.iter_rows(min_row=2):
        for cell in cells:
            if isinstance(cell.value,str):
                assert len(cell.value)<=32767,(name,cell.coordinate,len(cell.value))
                if cell.value.startswith('='):cell.data_type='s'
            cell.font=Font(name='Noto Sans CJK SC',size=10,color='243848')
            cell.alignment=Alignment(vertical='top',wrap_text=True)
            if cell.row%2==0:cell.fill=PatternFill('solid',fgColor='F2F6F9')
        ws.row_dimensions[cells[0].row].height=90 if name.startswith('工程化') else 76
    if rows:
        table=Table(displayName=table_name,ref=ws.dimensions)
        table.tableStyleInfo=TableStyleInfo(name='TableStyleMedium2',showRowStripes=False)
        ws.add_table(table)
        ws.auto_filter.ref=ws.dimensions
    ws.sheet_properties.outlinePr.summaryRight=False
    return ws


def build_workbook(builder):
    src=builder.source
    wb=Workbook();wb.remove(wb.active)
    main_rows=[]
    for r in builder.records:
        permission='N/A' if r['access']=='N/A' else r['access']+('（'+ACCESS[r['access']]+'）' if r['access'] in ACCESS else '')
        main_rows.append([r['id'],r['major'],r['minor'],r['title'],r['code'],' / '.join(r['groups']),r['required'],
                          r['condition'],permission,r['fmt'],r['unit'],r['enum'],r['dynamic'],r['english'],r['chinese'],
                          r['path'],r['status'],r['collection'],r['data_source'],r['multilingual'],r['example'],r['owner'],r['remarks']])
    main=make_sheet(wb,'工程化v4.0',MAIN_HEADERS,main_rows,MAIN_WIDTHS,'EngineeringV40','F2')
    main.column_dimensions.group('P','W',hidden=True)
    for r in builder.records:
        i=r['excel_row']
        fill={'M':'E2F0E8','C':'FFF0CA','V':'E7EFF8','N/A':'E8E8E8','待确认':'FFDDA0'}[r['required']]
        main.cell(i,7).fill=PatternFill('solid',fgColor=fill)
        main.cell(i,7).font=Font(name='Noto Sans CJK SC',size=10,bold=True,color='524019' if r['required']=='C' else '243848')
        if '待确认' in r['status']:
            for col in [5,7,9,17]:main.cell(i,col).fill=PatternFill('solid',fgColor='FFF1D4')
            main.cell(i,7).comment=Comment('沿用3.4业务口径：'+(r['old'][5] if r['old'] else '未提供')+'。当前语义包没有确认该旧工程项；不能当作已验证的官方required。','v4.0评审说明')
        elif r['added']:
            main.cell(i,5).fill=PatternFill('solid',fgColor='DEF1EE')
        if 'BMS' in r['condition']:
            main.cell(i,8).fill=PatternFill('solid',fgColor='E2EFFA')
        if r['required']=='N/A':
            for cell in main[i]:cell.font=Font(name='Noto Sans CJK SC',size=10,color='8B949B')
        if r['enum'].count('\n')>10:main.row_dimensions[i].height=150
        if len(r['chinese'])>400:main.row_dimensions[i].height=126
        if '待确认' in r['status']:
            main.cell(i,5).comment=Comment(r['remarks'],'语义映射待确认')
    main['A1'].comment=Comment('v4.0第一版评审稿。以工程化3.4的121条为业务底表，当前语义包做映射校核；黄色为待确认，青色为语义补齐。右侧P:W列可展开。','使用说明')
    main['F1'].comment=Comment('电池分类统一EV/LMT/IND；无BMS不是第四种类别。BMS、固定式储能、可充电、使用阶段等写在适用条件中。','使用说明')
    main['G1'].comment=Comment('M=当前条件下必填；C=条件／组合必填；V=源明确可选；N/A=类别不适用。旧表保留行的M/V仅沿用3.4并用黄色提示。JSON required及语义差异见规则页。','使用说明')
    main['M1'].comment=Comment('Static/Dynamic为工程更新机制建议，不是JSON Schema关键字。旧值和调整理由见v3.4变更对照；实际采集频率和触发需业务确认。','使用说明')
    main['P1'].comment=Comment('只读源资料后整理，未修改线上3.4。候选路径不是已确认映射，结构／子字段规则完整列在规则与映射。','使用说明')
    dv=DataValidation(type='list',formula1='"M,C,V,N/A,待确认"');dv.errorTitle='必填状态';dv.error='请选择M/C/V/N/A/待确认';dv.showErrorMessage=True
    main.add_data_validation(dv);dv.add('G2:G'+str(main.max_row))
    dv2=DataValidation(type='list',formula1='"Static,Dynamic,待确认"');main.add_data_validation(dv2);dv2.add('M2:M'+str(main.max_row))

    # Include the full vocabulary of every namespace used by actual field enums.
    used_ns={uri.rsplit('#',1)[0] for uri in [*builder.enum_usage,*builder.unit_usage]}
    enum_rows=[];enum_starts={}
    for uri,v in sorted(src.vocab.items(),key=lambda x:(dict_id(x[0]),x[1]['代码'])):
        if uri.rsplit('#',1)[0] not in used_ns:continue
        group=dict_id(uri);enum_starts.setdefault(group,len(enum_rows)+2)
        usage=join_unique([*sorted(builder.enum_usage.get(uri,set())),*sorted(builder.unit_usage.get(uri,set()))]) or '词表全集条目；当前字段未必允许全部条目'
        code=v['代码'];zh=ZH_CODES.get(code,'待翻译：'+v['官方名称'])
        enum_rows.append([group,'官方TTL词表',code,zh,v['官方名称'],uri,usage,
                          '字段只允许的子集以主表及源Schema／SHACL为准；传输使用完整URI。',v['官方英文定义'],v['来源']])
    enum_starts['CONST-TRUE']=len(enum_rows)+2
    enum_rows.append(['CONST-TRUE','JSON Schema固定值','true','必须设置为true','Fixed true','true','$.separateCollectionSymbol',
                      '源Schema const=true；boolean类型，不是字符串。','', 'product/batteries/1.0.0/schemas/pub-batteries.schema.json'])
    for legacy in src.legacy:
        old=legacy['raw'];raw=old[10]
        if not raw or raw=='/':continue
        gid='LEGACY-'+str(legacy['row']).zfill(3);enum_starts[gid]=len(enum_rows)+2
        for line in raw.splitlines():
            if not line.strip():continue
            match=re.match(r'(.+?)[:：]\s*([A-Za-z][A-Za-z0-9_-]*)\s*$',line.strip())
            label,value=(match.group(1),match.group(2)) if match else (line.strip(),'未给独立代码')
            enum_rows.append([gid,'3.4历史参考',value,label,'待核对',value,str(legacy['row'])+'行 '+legacy['code'],
                              '历史业务枚举，不能当作当前官方字段的允许值；未结构化内容保留原文。',line,URL+'；工程化3.4第'+str(legacy['row'])+'行'])
    enum_sheet=make_sheet(wb,'枚举字典',ENUM_HEADERS,enum_rows,ENUM_WIDTHS,'EngineeringEnumsV40','D2')
    for rec in builder.records:
        if rec['enum_id'] in enum_starts:
            cell=main.cell(rec['excel_row'],12);cell.hyperlink=f"#'枚举字典'!A{enum_starts[rec['enum_id']]}"
            cell.font=Font(name='Noto Sans CJK SC',size=10,color='176E91',underline='single')

    rules=[]
    info=[
        ('版本','v4.0第一版评审稿；生成时间 '+datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')),
        ('来源','参考《电池护照模型V3.4 260918》工程化3.4的121条业务记录；语义依据为本地2026-10-30资源快照。'),
        ('主表粒度','业务字段按规则拆行；相同规则合并电池类别。复合字段和结构组保留路径，全部子字段在本页展开。'),
        ('颜色','黄色=候选／旧业务待核对；青色=语义新增；必填列绿M、黄C、蓝V、灰N/A。'),
        ('分类','EV/LMT/IND三类；可充电、循环应用、固定式储能、有BMS是适用条件。'),
        ('必填','M/C/V/N/A/待确认；JSON required不直接等于全部业务必填，未列required不自动判V。'),
        ('旧业务','旧表未匹配项仍保留，M/V和单位等仅按3.4继承；未声称得到当前语义包或最新法规确认。'),
        ('权限','PUB公开；RES1合法权益人／公告机构／主管机关／欧委会；RES2公告机构／主管机关／欧委会；RES3合法权益人对单个电池。'),
        ('动静态','工程建议；周期采集和状态变化触发都可归Dynamic。Static可在资料或版本更正时维护。来源与采集方式另行确认。'),
        ('枚举','主表列字段允许值，字典页有完整URI。字典全集不意味着每个字段都允许全部词项。'),
        ('精度','Decimal精度来自旧工程表的建议，未转换成源Schema强制精度；不擅自增加0～100。'),
        ('BMS','工业相关指标适用条件为固定式储能且有BMS；where possible按指标可提供性判断。没有凭空加入hasBms传输字段。'),
        ('范围','电压valueRange是温度等适用条件范围，不是电压上下限；最低和最高电压是独立字段。'),
        ('状态','旧五种业务状态暂保留为internalDppStatus建议字段；官方dppStatus为validStatus/invalidStatus，需要确认内部到对外状态关系。'),
        ('校验范围','完成源数据完整性、语义节点覆盖、字段映射及Excel结构检查；未运行完整SHACL，未核验旧表未匹配字段的全部外部法规来源。'),
    ]
    for i,(title,text) in enumerate(info,1):rules.append(['填表说明','INFO-'+str(i).zfill(2),title,'','','','',text,'','','',URL if title=='来源' else '工程化v4.0整理规则','',''])
    targets=defaultdict(list)
    for rec in builder.records:
        if rec['entry']:
            for group in rec['groups']:targets[group,rec['path']].append(rec)
    # Every structural field and array item from all three models is represented.
    for (group,path),entry in sorted(src.index.items()):
        node=entry['node'];context=meaning(entry,src);text=node.get('description','')
        rid='SEM-'+str(len(builder.rule_ids)+1).zfill(4)
        builder.rule_ids[group,path]=len(rules)+2
        chain=entry['chain']
        req=('数组元素' if path.endswith('[]') else '是（父对象存在时）' if chain and chain[-1]['required'] else '未列入当前父对象required')
        structural={key:node[key] for key in ['format','pattern','minimum','maximum','minLength','maxLength','minItems','maxItems','minProperties','maxProperties','uniqueItems','const','required','anyOf','oneOf','dependentRequired','propertyNames','additionalProperties','unevaluatedProperties'] if key in node}
        # Documentation-only additionalProperties does not impose a value type.
        if isinstance(structural.get('additionalProperties'),dict):structural['additionalProperties']='源未限定动态值类型；语义要求文本'
        refs=targets.get((group,path),[])
        rules.append(['语义结构',rid,join_unique([r['code'] for r in refs]) or node.get('title',field_name(path)),group,path,format_of(node),req,
                      join_unique([section(context.get('description',''),'业务适用条件'),node.get('x-semantic-required-note',''),
                                   '存在条件：'+' → '.join(f["path"]+('必填' if f['required'] else '非独立required') for f in chain),
                                   section(text,'父级组合与语义规则')]),
                      join_unique(['JSON Schema：'+json.dumps(structural,ensure_ascii=False),
                                   'SHACL：\n'+section(text,'SHACL 规则摘录'),
                                   '规则差异／边界：\n'+section(text,'规则差异／边界')]),
                      join_unique([section(text,'单位／口径'),section(text,'单位填写方式'),
                      'enum='+json.dumps(enum_values(node),ensure_ascii=False) if enum_values(node) else '无封闭enum']),
                      join_unique([section(context.get('description',''),'官方英文定义'),
                                   section(context.get('description',''),'官方适用说明原文')]),
                      join_unique([*node.get('x-source-locations',[]),*context.get('x-source-locations',[]),section(text,'SHACL／词表来源')]),
                      join_unique([r['oldrow'] for r in refs if r['oldrow']],', '),join_unique(['第'+str(r['excel_row'])+'行' for r in refs],', ')])
    for rec in builder.records:
        if not rec['entry']:
            rules.append(['旧业务待核对','LEG-'+str(rec['oldrow']),rec['code'],' / '.join(rec['groups']),rec['path'],rec['fmt'],
                          '当前语义包未确认',rec['condition'],rec['remarks'],rec['unit']+'\n'+rec['enum'],rec['english'],
                          URL+'；工程化3.4第'+str(rec['oldrow'])+'行',rec['oldrow'],'第'+str(rec['excel_row'])+'行'])
    rule_sheet=make_sheet(wb,'规则与映射',RULE_HEADERS,rules,RULE_WIDTHS,'EngineeringRulesV40','E2')
    rule_sheet.column_dimensions.group('I','N',hidden=True)
    for row in range(2,len(info)+2):
        for cell in rule_sheet[row]:cell.fill=PatternFill('solid',fgColor='E4F1EE')
    for rec in builder.records:
        key=(rec['groups'][0],rec['path'])
        if key in builder.rule_ids:
            cell=main.cell(rec['excel_row'],16);cell.hyperlink=f"#'规则与映射'!E{builder.rule_ids[key]}"
            cell.font=Font(name='Noto Sans CJK SC',size=10,color='176E91',underline='single')

    diffs=[]
    for legacy in src.legacy:
        rs=builder.by_legacy[legacy['row']];old=legacy['raw']
        def vals(key):return join_unique([' / '.join(r['groups'])+'：'+str(r[key]) for r in rs])
        notes=join_unique([r['remarks'] for r in rs])
        diffs.append([legacy['row'],old[3],legacy['code'],join_unique(['第'+str(r['excel_row'])+'行' for r in rs],', '),
                      join_unique([r['status'] for r in rs]),old[4],join_unique([' / '.join(r['groups']) for r in rs]),old[5],vals('required'),
                      old[6],vals('access'),old[7],vals('fmt'),old[9],vals('unit'),old[10],vals('enum'),old[13],vals('dynamic'),
                      join_unique([r['path'] for r in rs]),join_unique([
                      ('旧表未给机器编码；本稿采用官方语义名称hazardousSubstances。' if legacy['row']==64 else
                       '旧表缺少机器编码，本稿提出：'+legacy['code']+'，需业务确认。') if not legacy['old_code'] else '',notes])])
    for rec in builder.records:
        if not rec['added']:continue
        diffs.append(['新增','—',rec['code'],'第'+str(rec['excel_row'])+'行',rec['status'],'—',' / '.join(rec['groups']),'—',rec['required'],
                      '—',rec['access'],'—',rec['fmt'],'—',rec['unit'],'—',rec['enum'],'—',rec['dynamic'],rec['path'],rec['remarks']])
    diff_sheet=make_sheet(wb,'v3.4变更对照',DIFF_HEADERS,diffs,DIFF_WIDTHS,'EngineeringDiffV40','F2')
    diff_sheet.column_dimensions.group('L','S',hidden=True)
    for i,legacy in enumerate(src.legacy,2):
        cell=diff_sheet.cell(i,4);rs=builder.by_legacy[legacy['row']]
        cell.hyperlink=f"#'工程化v4.0'!E{rs[0]['excel_row']}";cell.font=Font(name='Noto Sans CJK SC',size=10,color='176E91',underline='single')
    wb.properties.title='电池护照工程化字段表 v4.0 评审稿'
    wb.properties.creator='Codex'
    wb.properties.description='基于工程化3.4与2026-10-30语义快照；保留旧业务，明确候选映射、规则来源、类别条件及工程建议。'
    wb.active=0
    return wb,dict(mainRows=len(main_rows),enumRows=len(enum_rows),semanticRuleRows=len(src.index),ruleRows=len(rules),diffRows=len(diffs))


def verify(path,builder,metrics):
    wb=load_workbook(path,data_only=False)
    assert wb.sheetnames==['工程化v4.0','枚举字典','规则与映射','v3.4变更对照']
    assert wb['工程化v4.0'].max_row==metrics['mainRows']+1
    assert wb['v3.4变更对照'].max_row==metrics['diffRows']+1
    assert set(builder.by_legacy)==set(range(2,123))
    keys=set()
    for rec in builder.records:
        for group in rec['groups']:
            key=(rec['code'],group,rec['path'])
            assert key not in keys,('duplicate engineering mapping',key)
            keys.add(key)
        assert rec['required'] in {'M','C','V','N/A','待确认'}
        assert rec['english'] and rec['chinese'] and rec['fmt'] and rec['unit'] and rec['enum']
        if rec['entry']:
            for group in rec['groups']:assert (group,rec['path']) in builder.source.index
    assert len(builder.rule_ids)==642
    for ws in wb:
        assert ws.freeze_panes and ws.auto_filter.ref
        assert not any(cell.data_type=='f' for row in ws for cell in row)
        assert all(cell.data_type!='e' for row in ws for cell in row)
    for code,group,want in [('remainingCapacity','EV','N/A'),('remainingCapacity','LMT','M'),('remainingCapacity','IND','C'),
                            ('cycleLifeDischargeCRate','EV','V'),('warrantyPeriodOfTheBattery','EV','C')]:
        match=[r for r in builder.records if r['code']==code and group in r['groups']]
        assert match and match[0]['required']==want,(code,group,match)
    assert any(r['code']=='internalDppStatus' and not r['entry'] for r in builder.records)
    assert any(r['code']=='dppStatus' and r['entry'] for r in builder.records)
    assert not any(r['code']=='hazardousSubstanceNames' for r in builder.records)
    assert any(r['code']=='hazardousSubstances' and r['path']=='$.hazardousSubstances' for r in builder.records)
    for group in ['LMT','IND']:
        assert any(r['code']=='selfDischargingRateSet' and group in r['groups'] for r in builder.records)
    return wb


def main():
    source=Source();builder=Builder(source);builder.build_records()
    wb,metrics=build_workbook(builder)
    OUT.mkdir(exist_ok=True)
    inputs=OUT/'参考输入';inputs.mkdir(exist_ok=True)
    (inputs/'工程化3.4_读取快照.json').write_bytes(REF.read_bytes())
    output=OUT/'电池护照_工程化字段表_v4.0_评审稿.xlsx'
    wb.save(output)
    verify(output,builder,metrics)
    report={
        'version':'4.0-review.1','referenceUrl':URL,'referenceSheet':'工程化3.4',
        'referenceReadRange':'A1:S500','referenceHasMore':False,'referenceRowsPreserved':121,
        'sourceSemanticVersion':'2026-10-30','profileTopLevelFields':{'EV':63,'LMT':66,'IND':66},
        **metrics,'originalBusinessRowsAllRepresented':True,'semanticPathsAllRepresented':len(builder.rule_ids)==642,
        'mappedMainRows':sum(r['entry'] is not None for r in builder.records),
        'legacyReviewRows':sum(r['entry'] is None and r['required']!='N/A' for r in builder.records),
        'newSemanticRows':sum(r['added'] for r in builder.records),'statusCounts':dict(Counter(r['status'] for r in builder.records)),
        'requiredCounts':dict(Counter(r['required'] for r in builder.records)),
        'verification':'passed','onlineOriginalModified':False,'officialShaclExecution':False,
        'sourceSha256':{'engineering34Snapshot':sha(REF),**{g:sha(ROOT/p) for g,p in PROFILE_FILES.items()}},
        'outputSha256':sha(output),
    }
    (OUT/'生成与校验结果.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (OUT/'主表记录索引.json').open('w',encoding='utf-8') as file:
        json.dump([{k:r[k] for k in ['id','code','title','groups','required','path','status','oldrow','added','excel_row']} for r in builder.records],file,ensure_ascii=False,indent=2)
    (OUT/'先读我.txt').write_text(
        '第一版评审稿：打开 电池护照_工程化字段表_v4.0_评审稿.xlsx，先看工程化v4.0。\n'
        '保留原3.4的121条业务记录，按类别及规则差异拆行，并补充当前语义字段。\n'
        '黄色=旧业务或候选映射待核对；青色=语义新增。主表P:W列默认分组收起，可点击上方“+”展开。\n'
        '旧业务未匹配项的M/V、格式和单位沿用3.4，尚未由当前语义包确认；不会冒充为官方Schema结论。\n'
        'M/C/V/N/A与JSON required分别记录；BMS、可充电、固定式储能及使用阶段等条件见适用条件。\n'
        '动静态是工程建议，旧值及建议调整理由见变更对照。数据来源、采集频率和责任部门未确认处明确待确认。\n'
        '语义结构、数组、动态语言键等规则均有来源，长枚举请点击主表枚举项进入枚举字典。\n'
        '本次只生成本地Excel，未修改钉钉原表。未对旧表未匹配字段重新核验全部法规来源，未执行完整SHACL。\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2));print(output)


if __name__=='__main__':main()
