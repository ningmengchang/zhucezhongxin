from pathlib import Path
import json
import math
import re
from collections import Counter

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.comments import Comment
from openpyxl.utils import get_column_letter


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / '官方指南3.2 Digital Batteries Passport - data point by category.pdf'
DATA = json.loads((ROOT / '指南V3.2_71项原文结构化.json').read_text())
META = json.loads((ROOT / '来源与下载校验.json').read_text())
assert [r['item'] for r in DATA] == list(range(1, 72))
assert META['identicalToOfficialPdf'] is True

# 中文均为本次分析；英文矩阵保留来源内容，只将 PDF 的换行整理为空格。
LABELS = '''唯一产品标识（UPI）
注册及／或负责电池护照的主体名称
制造商名称、注册商号或注册商标（至少一项）
制造商单一联系邮寄地址
制造商网站与电子邮箱（均为可选）
电池类别
型号／批次／序列号／产品编号等识别信息
制造地点
制造年月
重量
容量
电池化学体系
除汞、镉、铅之外的有害物质
可用灭火剂
质量占比超过 0.1% 的关键原材料
材料组成综合项（避免重复）
碳足迹声明
碳足迹标签
负责任采购及电池尽职调查报告信息
再生钴占比
再生锂占比
再生镍占比
再生铅占比
可再生含量占比
额定容量（与第 11 项重复）
最低电压及适用时的温度范围
标称电压及适用时的温度范围
最高电压及适用时的温度范围
初始功率能力
功率限值及适用时的温度范围
预期循环寿命
预期循环寿命的参考测试
容量耗尽阈值
非使用状态下可承受温度范围及参考测试
日历寿命商业质保期
初始往返能量效率
循环寿命达到 50% 时的往返能量效率
电芯内阻与电池包内阻
循环寿命测试的充电 C 倍率
循环寿命测试的放电 C 倍率
第 13(4) 条的分类收集标识
第 13(5) 条的镉／铅标识
欧盟符合性声明
废电池预防与管理信息
可打印、下载与保存的使用说明
详细组成（正极、负极、电解液等）
部件编号及替换备件来源联系方式
拆解信息
安全措施
证明合规的测试报告结果
额定容量（单体性能项，引用第 11 项）
容量衰减
功率
功率衰减
内阻
内阻增加
适用时的往返能量效率
适用时的往返能量效率衰减
参考条件下预期寿命（循环次数）
参考条件下预期寿命（年）
认证能量状态 SOCE
剩余容量
可获取时的剩余功率能力
可获取时的剩余往返能量效率
可获取时的自放电率变化
可获取时的欧姆内阻
电池状态
充电与放电循环次数
事故等负面事件
运行环境条件（含温度）的周期记录
荷电状态 SOC 的周期记录'''.splitlines()
assert len(LABELS) == 71

DETAILS = {}


def detail(n, meaning, implementation, caution='', mapping='按语义映射；不能仅凭旧编号连接字段。'):
    DETAILS[n] = (meaning, implementation, caution, mapping)


detail(1, '公开必填的唯一产品标识。', '管理标识唯一性，将单体电池、护照及二维码解析关系关联；结合第 77 条确定适用范围。', '不同于型号、企业标识、DPP 资源状态；第 7 项的识别信息选填组合不替代 UPI。')
detail(2, '公开身份信息按名称填写，原文括号写 name。', '配置负责／注册主体名称，并在内部关联主体主数据、凭证及管理范围。', '名称显示要求不表示内部身份、认证、注册流程可以只凭企业名称；也不能据此删除 BatteryPass 的主体 URI。')
detail(3, '制造商名称、注册商号或商标至少提供一项。', '按“至少一项非空”校验，展示实际提供的名称或商标。', '不是要求三项全部必填；制造商与负责投放市场／注册主体可能不同。')
detail(4, '提供一个联系邮寄地址。', '保存并展示可联系的完整地址，保持与制造商主数据关联。', '指南未强制规定地址 JSON 字段结构，也未以网站替代邮寄地址。')
detail(5, '网站和邮箱均为可选；已有这些数据时填写。', '两项独立配置与格式校验；允许没有可用信息的记录通过。', '不能将网站、邮箱都设为所有电池统一强制字段。')
detail(6, '指南表按 EV、LMT、Industrial 三个法定类别组织。', '保存法定类别；在工业类别下另保留固定式、可充电、有无 BMS 等属性。', '原文写 3-code list，但没有给出必须采用的 API 枚举字符串。')
detail(7, '型号、批次或序列号、产品编号或其他识别要素，至少一项。', '设置识别信息组合校验，并关联实际型号、批次与 SN 主数据。', '这只是该行的填写口径。第 77(1) 条针对每个适用电池的护照义务不能被改成一个型号共用一本护照。')
detail(8, '制造工厂的城镇、地区、国家／司法管辖区。', '结构化保存地点并提供可读展示；支持工厂关联和地点快照。', '字段说明跨 PDF 第 5—6 页，不能只保留第一页半句。')
detail(9, '制造日期按月和年。', '支持 YYYY-MM 这类月精度实现，并明确精度；该格式是实施建议。', '原文未要求精确到某日，不应凭空补 01 日。')
detail(10, '重量的基准单位为 kg。', '数值与单位一起保存；允许原文说明中的单位倍数／分数并可靠换算。', '统一比较时保留原始数值与换算依据。')
detail(11, '容量的基准单位为 Ah。', '设置容量基准数据，关联第 25、51 项的语义与版本。', 'Ah 与 kWh 不是同一物理量，不能把能量直接改字段名当容量。', '旧索引 #57 Rated capacity 同时映射旧指南 11、25、51；应保留一对多语义关系。')
detail(12, '化学体系按代码表表达。', '建立带版本的化学体系词表和映射，保留扩展与审核能力。', '本文件没有列出完整代码表，不能把自拟枚举冒充官方列表。')
detail(13, '说明电池中除汞、镉、铅之外的有害物质，原文为自由文本。', '保存可读说明及依据；与受限的详细组成分别控制公开范围。', '不能用“公开有害物质说明”直接开放完整商业配方。')
detail(14, '可用灭火剂，原文为自由文本。', '维护安全信息的说明、来源与更新；公开展示此项。', '它和第 49 项受限的安全措施不是同一权限对象。')
detail(15, '质量占比超过 0.1% 的关键原材料，按代码表表达。', '按法规材料范围及阈值计算是否列出，并保留材料词表版本。', '0.1% 是判断条件，指南没有在本项要求公开全部材料的精确比例。')
detail(16, '综合材料组成与前述数据重复，指南要求不重复填显。', '复用第 12、13、15 项；保留法规映射而避免重复表单。', '免重复不等于免除原数据；第 46 项详细组成仍单独存在。')
detail(17, '按本版，2027 年 2 月暂不填显碳足迹声明，格式待实施法明确。', '保留数据采集和附件能力，以生效日期／适用范围配置发布校验。', '这是本版护照填显口径，不能扩张为所有碳足迹法律义务均取消。')
detail(18, '按本版，2027 年 2 月暂不填显碳足迹标签，格式待明确。', '标签资源与相关数据保留扩展点，适用规则跟随正式文件更新。', '不要把尚未要求填显的内容设成护照启用的统一阻断条件。')
detail(19, '按本版，2027 年 2 月暂不填显；注明按第 48(1) 条自 2027 年 8 月适用。', '配置主体及电池适用条件、时间和报告引用；到期前进行法规复核。', '指南中的后续日期应与当时有效法规核对，不是所有主体无条件同日必填。')
for n, metal in [(20, '钴'), (21, '锂'), (22, '镍'), (23, '铅')]:
    detail(n, f'再生{metal}占比，单位为百分比；本版注明 2027 年 2 月暂不填显，按第 8 条适用。', '保留材料来源和比例数据；按电池类别、材料与法规生效时间配置填显规则。', '不能把暂不填显解释为永不需要，也不能用 0 代替尚未取得的真实测量值。')
detail(24, '可再生含量占比，三类均为必填。', '作为独立百分比语义保存，明确供应链依据和计算口径。', 'renewable 与 recycled 不同；第 20—23 项的当期规则不能套用到本项。')
detail(25, '额定容量与第 11 项重复，指南要求不重复填显。', '复用经确认的容量数据及单位，保留两个法规来源。', '不能为满足两个编号而收集两套互相矛盾的同义数据。')
for n, word in [(26, '最低'), (27, '标称'), (28, '最高')]:
    detail(n, f'{word}电压，单位 V；温度范围在相关时提供，单位 °C。', '电压数值与测试／适用温度条件分别保存，并校验同一条件下的数据关系。', '温度是有条件信息，不能将其一律设为强制非空。')
detail(29, '初始功率能力，单位 W。', '记录基准功率、测量条件及证据，和使用阶段功率区分。', '原文未统一定义所有技术条件，不能把业务假设写成指南要求。')
detail(30, '功率限值，单位 W；相关时给温度范围。', '支持功率限制及对应条件，复用温度范围结构。', '指南没有给出峰值／持续功率等完整 API 枚举。')
detail(31, '预期寿命以循环次数表达；部分工业电池须能适用循环寿命。', '记录循环寿命值和适用性判断，非循环应用走有依据的不适用流程。', '工业电池不是全类统一必填。')
detail(32, '预期循环寿命使用的参考测试，自由文本。', '与第 31 项绑定保存参考测试及版本。', '不得只有循环次数而没有本项要求的测试说明。')
detail(33, '容量耗尽阈值，单位 %；EV 必填，LMT 与工业栏为不填显。', '按类别分别配置，避免沿用 EV 模板阻断其他类别。', '不能把所有电池健康阈值都固定为某个惯用百分比。')
detail(34, '非使用状态的耐受温度上下限，并说明参考测试。', '分别保存上下限和参考测试说明。', '不是第 70 项使用阶段的实测温度历史。', '旧索引 #88/#89 把上下限关联到旧指南 34/35；V3.2 两个边界均应映射 34，35 是质保期。')
detail(35, '有商业日历寿命质保时提供期限，单位月。', '先记录是否提供商业质保，有则填写期限；无则记录不适用依据。', '不能将所有没有商业质保的电池判为数据不合格。')
detail(36, '初始往返能量效率，单位 %；工业栏仅部分电池适用。', '记录效率和测试条件，工业适用性按技术特征及依据确定。', '指南未列出“部分工业电池”的穷尽定义，需要规则依据。')
detail(37, '达到循环寿命 50% 时的往返能量效率，单位 %。', '作为明确测试条件下的独立值管理，关联循环寿命与测试证据。', '不能自动把初始效率复制成该值；工业适用性需核对。')
detail(38, '同一编号含电芯内阻和电池包内阻两个值，单位 Ω。', '保存两个可区分的值及测试条件，原文矩阵在本工作簿合为一个编号行。', '71 个编号不等于 71 个原子 API 字段。')
detail(39, '相关循环寿命测试的充电 C 倍率，数值。', '将充电倍率与测试引用关联，与放电倍率分别保存。', '工业栏仅部分电池适用；不要继续用一个含义不清的通用倍率覆盖两个方向。', '旧索引 #85 C-rate 对应旧指南 39；V3.2 需明确为充电倍率。')
detail(40, '相关循环寿命测试的放电 C 倍率；三个类别均为可选。', '提供独立可选字段，兼容既有数据和测试说明。', '欧委会更新说明明确新增本项；不能把可选项升级为统一强制项。', '新增指南项；需另做模型映射，不能只把旧 40 号字段重命名。')
detail(41, '第 13(4) 条分类收集标识，按 Yes/No 表达。', '保存是否满足相应标识要求及标签证据，提供可读展示。', '不是简单保存任意二维码是否存在。')
detail(42, '第 13(5) 条镉／铅符号，在适用时提供，按代码表表达。', '按材料及法律适用条件判定符号，维护标识与代码映射。', '没有触发条件不能硬填 Cd／Pb；具体阈值应另查有效法规。')
detail(43, '欧盟符合性声明，以网页链接提供。', '公开链接应可用、对应正确产品／型号并受版本管理。', '指南未给出统一附件格式；公开声明与第 50 项受限报告应分开。')
detail(44, '第 74(1)(a)—(f) 项的废电池预防与管理信息，多段自由文本。', '按各主题组织可读信息并保存来源，避免只给一个无含义的布尔值。', '不能因附件或链接已有内容而遗漏实际须提供的主题。', '旧索引若仍以旧指南 43 指向废电池管理，需改映射到新 44；安全措施还要区分新 49。')
detail(45, '本版写明 2027 年 2 月暂不填显，标注 TBC 并提到 Omnibus 待采纳。', '保留说明书能力，并以版本配置控制护照校验；上线前复核正式法源。', '指南对待采纳修法的描述不能单独证明现行法律义务已经暂停或修改。')
detail(46, '详细组成，包括正极、负极及电解液材料，受限且必填。', '独立管理详细组成；仅向获准主体返回，避免通过导出或附件公开。', '第 16 项免重复不适用于本项；合法权益人资格不是一个可自选的全局角色。', '旧索引 #43 对应旧指南 45 的详细组成，V3.2 应映射新 46。')
detail(47, '部件编号和替换备件来源联系方式合为一项，以网页链接提供。', '链接落地内容覆盖部件与采购来源；其访问与下载继承受限权限。', '编号合并不等于两个业务字段必须物理合成一列。', '旧索引 #46（旧指南 46）与 #47（旧指南 47）均映射 V3.2 新 47。')
detail(48, '拆解图、顺序、紧固方式与数量、工具、损坏风险及电芯数量和布局等，以链接提供。', '维护涵盖各要素的拆解文档与版本，受限授权时才开放。', '只上传一张拆解图不一定覆盖列出的全部内容。')
detail(49, '安全措施，以链接提供，受限。', '将受限安全文档与公开灭火剂说明分别建权限对象。', '本版将合法权益人、公告机构、成员国主管机关及欧委会列入该组访问者。')
detail(50, '证明符合相关要求的测试报告结果，以链接提供。', '向公告机构、成员国主管机关及欧委会配置相应访问路径。', '本项未给一般合法权益人开放，不能把所有 Restricted 归为同一角色。')
detail(51, '单体性能项额定容量注明与第 11 项相同；工业只限可充电。', '复用来源明确且版本匹配的基准容量，保留单体性能和公开基准的授权语义。', '不要额外要求重复填表，也不要用权限合并开放其他单体受限数据。')
detail(52, '容量衰减，单位 %；工业只限可充电。', '记录单体、测量时间、参考基准与衰减口径。', '缺失不是 0%；衰减值需要可解释的基准。')
detail(53, '单体功率，单位 W；工业只限可充电。', '与初始功率区分，记录单体版本及适用条件。', '不能用型号初始功率长期冒充每个单体当前值。')
detail(54, '功率衰减，单位 %；工业只限可充电。', '关联基准功率及测量状态，核对衰减计算口径。', '不把不同测试条件下的功率直接相除。')
detail(55, '单体内阻，单位 Ω；工业只限可充电。', '保存测量对象、条件、时间与证据。', '与第 38 项电芯／电池包基准内阻区分。')
detail(56, '内阻增加，单位 %；工业只限可充电。', '关联参考内阻并保留计算依据。', '增加比例不是绝对内阻，单位不能混用。')
detail(57, '适用时提供往返能量效率，单位 %；工业须为可充电且适用。', '记录适用性及效率，未适用时保留依据。', '原文自带 where applicable，不是所有单体无条件必填。')
detail(58, '适用时提供往返能量效率衰减，单位 %。', '与初始／基准效率关联，分别处理当前效率和衰减。', '工业需同时满足可充电和技术适用条件。')
detail(59, '参考设计条件下预期寿命，以循环次数表达，排除非循环应用。', '按单体状态管理参考条件及值；工业先判断可充电和循环应用。', '结合第 18 页备注，状态改变时可能需更新；不等于要求固定高频上报。')
detail(60, '参考设计条件下的预期寿命，以年表达；工业只限可充电。', '与循环寿命分别保存，并关联参考条件和状态版本。', '年份长度不是制造年份；不能直接当生产日期字段。')
detail(61, 'EV 的认证能量状态 SOCE，单位 %；LMT、工业栏不填显。', '为 EV 建立专门健康指标，保留计算／认证依据。', 'SOCE 不是任意 SOH 百分比的同义词，不能直接改名或换算冒用。')
detail(62, '剩余容量，单位 Ah；LMT 必填，工业仅固定式储能且有 BMS，EV 栏不填显。', '按电池子类与 BMS 属性选择字段，记录来源和测量时间。', '不能把 EV 栏不填显扩大解释为其他法规或业务场景禁止收集；不能用 kWh 直接代替 Ah。', '旧索引 #58 Remaining capacity 的自愿项／必填项标记应与当前指南分别维护。')
for n, what in [(63, '剩余功率能力（W）'), (64, '剩余往返能量效率（%）'), (65, '自放电率变化（时间戳、每年下降百分比）'), (66, '欧姆内阻（Ω）')]:
    detail(n, f'{what}；EV 栏不填显，LMT 栏写 Mandatory；工业仅固定式储能且有 BMS。', '保留适用性、技术可获取性、数据来源及未获取原因；提供单位和时间等必要元数据。', '名称同时有 where possible 限定，不能忽略，也不能擅自把 Mandatory 改为 Optional；正式校验口径需结合第 14 条、附录 VII 及权威解释核对。', '旧模型中的 C/V/M 等项目标记不能直接等同 V3.2 表列口径，需逐项复核。')
detail(67, '单体电池状态代码：original、repurposed、re-used、remanufactured、waste。', '按生命周期变更并留历史；关联重新评估后的性能数据和负责主体。', '这是电池状态，不是护照资源的 active／archived／invalid 状态；复用、再利用等中文译名需统一。')
detail(68, '充电和放电循环次数，从开始使用时适用。', '按单体维护循环数、数据时点与来源；区分计数或等效循环口径。', '指南未要求统一上报间隔，不能从周期数据自行推定“每天一次”。')
detail(69, '事故等负面事件，多条自由文本，从使用开始时适用。', '建立事件记录，关联单体、发生时间、来源和处理状态。', '没有事故与尚未收集不同；无需虚构一条事故填满字段。')
detail(70, '运行环境条件包括温度，以代码、直方图及相关单位表达，使用后适用。', '支持区间、频数／时长、统计时间段及单位，按选定互操作方案确定具体结构。', '原文没有规定统一分箱和采样周期；只给一个当前温度不能表达周期分布。')
detail(71, 'SOC 的周期记录，以直方图和百分比表达，使用后适用。', '支持荷电状态分布及统计时段；与瞬时 SOC 值分开。', '百分比区间、频数／时长及归一化规则要写清；具体分箱是实施约定。')
assert set(DETAILS) == set(range(1, 72))


def applicability(s):
    if s == 'Mandatory': return '必填（仍须结合名称中的条件）'
    if s == 'Optional': return '可选'
    if s.startswith('Optional,'): return '可选；已有信息时填写'
    if 'repetition' in s: return '重复项，无需重复填显'
    if 'as of February 2027' in s:
        if 'Article 48' in s: return '2027-02 暂不填显；文中注明自 2027-08 起按第 48 条适用'
        if 'Article 8' in s: return '2027-02 暂不填显；后续依第 8 条适用'
        if 'Omnibus' in s: return '2027-02 暂不填显；TBC，待修法状态核验'
        return '2027-02 暂不填显；待后续实施法格式'
    if s == 'Not to be filled/displayed': return '本类别不填显（指南口径）'
    if s.startswith('Same as data point 11'):
        return '复用第 11 项；工业仅限可充电' if 'rechargeable' in s else '复用第 11 项容量'
    if 'commercial warranty' in s: return '有商业日历寿命质保时适用'
    if s == 'If applicable': return '适用时填写'
    if s.startswith('Cadmium'): return '镉／铅符号在适用时提供'
    if s.startswith('Applicable as soon'): return '电池开始使用后适用'
    if s.startswith('Only applicable for some industrial'):
        return '仅适用可用循环表达寿命的部分工业电池' if 'cycles' in s else '仅部分工业电池适用，技术条件需明确'
    if s.startswith('Only for rechargeable'):
        if 'non-' in s: return '仅可充电工业电池；排除非循环应用'
        if 'if applicable' in s: return '仅可充电工业电池，且技术上适用时'
        return '仅可充电工业电池'
    if s.startswith('Only for stationary'): return '仅固定式储能系统电池，且具有 BMS'
    raise ValueError(s)


def access(n):
    if n == 1: return '公开；唯一产品标识与单体护照关联'
    if n <= 45: return '公开；型号信息为主，按具体字段语义关联'
    if n <= 49: return '受限；合法权益人、公告机构、成员国主管机关、欧委会'
    if n == 50: return '受限；公告机构、成员国主管机关、欧委会'
    return '受限；合法权益人；原文明确为单个电池信息'


REQS = [
    ('G32-01', 'P0', '字段适用性规则', '将电池类别、是否工业、可充电、是否固定式、BMS、是否投入使用及规则生效日期作为独立属性。验证同一个字段在不同电池上的要求不同。', '6；31—40；51—71', '指南有类别／条件；规则实现为建议。', '扩展现有字段配置，不能只保留一个全局 required。'),
    ('G32-02', 'P0', '有条件和缺失原因', '区分必填、可选、条件项、当期不填显、重复引用；未提供时保存未适用／未获取／待核验原因，避免统一填 0。', '5；16—25；35；45；57—66', '指南状态明确；状态码和记录格式为建议。', '改造表单、导入校验、发布校验及错误提示。'),
    ('G32-03', 'P0', '主体名称与内部身份分别管理', '公开展示第 2 项名称；内部关联负责主体、注册主体、认证身份和管理范围，名称变更不丢失历史关系。', '2—5', '指南对外名称要求；内部管理方式为建议。', '保留主体主数据和 URI 等内部键，不因 name 删库字段。'),
    ('G32-04', 'P0', '识别信息与单体护照关联', '第 7 项执行至少一项校验，同时保证适用电池的 UPI、单体和护照关联及唯一性。', '1；7；BR 第 77 条', '字段指南与法规范围分别核对；关联实现为建议。', '不能用第 7 项的选项规则取消 SN 范围或单体护照能力。'),
    ('G32-05', 'P0', '访问分类细分', '公开、46—49 组、50 项测试报告、51—71 单体信息分别授权；后端统一覆盖 API、导出、附件及历史版本。', '46—71；备注 B', '指南明确访问分类；身份资格和具体法定权限须依有效法源。', '现有字段—角色体系保留，并补主体资格、操作和资源范围。'),
    ('G32-06', 'P0', '单体数据权限范围', '用户读取单体性能、健康、状态及使用数据时验证目标电池的资格／授权范围；覆盖到期撤销后的访问。', '51—71', '指南明确单体信息；范围与撤销控制结合已确认的 EN18239 设计。', '车主按有资格的 SN；维修厂型号授权仍需核验其范围依据。'),
    ('G32-07', 'P0', '更新旧版指南映射', '确认新增放电倍率 40、详细组成 46、零件与备件来源合并 47、废电池管理 44 等语义；用稳定字段键保存各版本映射。', '34；39—47', '本地旧索引和 V3.2 实文对照；不是已完成全部 Schema 审计。', '现有四类电池 OpenAPI 不直接覆盖，先形成映射再评估版本。'),
    ('G32-08', 'P0', '容量与效率语义及单位', '容量 Ah、能量、功率 W、内阻 Ω、比例 % 分开；核对 11／25／51 复用关系，保留原值和换算依据。', '10—11；25—30；38；51—66', '指南给单位和引用关系；存储与换算实现为建议。', '校核已有 remainingCapacityKWh 等示例与指南 Ah 的语义差异。'),
    ('G32-09', 'P1', 'EV 与 LMT／工业健康指标', 'EV 配置 SOCE；LMT 配置剩余容量等；工业 62—66 仅固定式且有 BMS。63—66 保留 where possible 与 Mandatory 的双重表述供核验。', '61—66', '指南明确类别；条件解释尚有需核对之处。', '禁止四类电池共用一张无条件 SOH 必填模板。'),
    ('G32-10', 'P1', '基准及生命周期版本', '保存 51—60 参考条件与数据版本；状态变化时评估是否更新，同时保存证据和历史。', '51—60；67；备注 A', '备注说“可能需更新”；更新机制为建议。', '电池状态与护照资源状态分开，避免两个枚举混用。'),
    ('G32-11', 'P1', '使用阶段与周期统计', '68—71 自使用开始适用；采集循环、事件和温度／SOC 分布；保存时间段、统计口径和数据来源。', '68—71', '指南给周期信息和直方图；采样周期、分箱实现为约定。', '现有瞬时温度或 SOC 单值不足以表达周期分布。'),
    ('G32-12', 'P1', '受限文档链接', '公开符合性声明与受限拆解、安全、测试报告分别管理；对受限文档下载本身执行授权。', '43；47—50', '指南要求链接及访问分类；链接治理为建议。', '不能只隐藏列表，却保留公开可长期直连的附件。'),
    ('G32-13', 'P1', '受控词表与格式版本', '为类别、化学体系、关键材料和状态维护稳定代码及含义；无法从指南得出的代码不宣称官方。', '6；12；15；42；67；70', '指南标出 code list；多数并未给完整值域。', '继续保留业务 Schema，增加来源版本和映射。'),
    ('G32-14', 'P1', '后续要求按版本启用', '对碳足迹、再生材料、尽调与说明书维护适用日期和法律依据；发布前复核后续正式文件。', '17—23；45', '指南的当期填显状态不是取消／修改法律义务。', '降低当期错误阻断，保留未来升级能力。'),
    ('G32-15', 'P1', '数据来源与证据', '对关键参数关联提供者、测试、时间和审核证据；与既有 EN18246 来源和完整性设计衔接。', '29—39；46；50—71', '证据管理为实施建议；指南不规定固定签名格式。', '本文件不能推出必须新增 dppHeader、改 JSON-LD 或使用某种 ESDC。'),
]

ISSUES = [
    ('I01', '63—66 的条件与表列强度', '名称写 where possible，LMT 栏写 Mandatory；必须同时保留并结合第 14 条、附录 VII 及权威解释确定实际校验。', 'P0：先明确适用性和不能测得时的处置，不擅自改成可选。'),
    ('I02', '工业电池子类', '31、32、36、37、39 的“部分工业电池”未在本表穷尽；51—60 的可充电限定与 62—66 固定式且 BMS 条件也不能混用。', 'P0：产品及法规人员给出各条件的判定依据。'),
    ('I03', '说明书与修法状态', '45 提到 Omnibus 待采纳并写 TBC；本指南不能单独证明现行法律已暂停。', 'P1：按适用时点查正式法规、过渡期与相关义务。'),
    ('I04', '代码表、URI 与 API 键', '3-code list／code list 不是可直接导入 Apifox 的完整枚举；本文件没有规定每项 JSON 键与 dictionaryReference。', 'P1：选定语义资源与实现配置后建立映射，并保留来源。'),
    ('I05', '动态更新频率', '68—71 说使用后适用、周期记录；未统一写成每天一次；51—60 说状态变化时可能更新。', 'P1：按法规、测量能力和选定互操作方案定义各类频率。'),
    ('I06', '直方图结构', '70／71 没给统一区间、频数／时长、归一化和统计周期 JSON Schema。', 'P1：与上下游接口约定一致；实现方案不冒充指南原文。'),
    ('I07', '合法权益人的精确范围', '本版第 18 页注明细节另由实施法澄清。单靠一个同名角色不能证明适用的法定访问资格。', 'P0：依据有效法源和已核验资格配置访问范围。'),
    ('I08', '旧模型字段编号', '本次确认了部分旧索引映射漂移；92 行 BatteryPass 属性不等于 71 个指南编号，且存在一对多关系。', 'P1：实施修改前对每个属性按语义和版本复核，不能按相同编号直接连接。'),
]

OVERVIEW = [
    ('结论', '这是按电池类别解释数据点及访问属性的非约束性指南，可用于准备采集和校验；不新增法律义务。'),
    ('原文标题', 'Guidance Document: Digital Batteries Passport – data points by category'),
    ('版本与日期', 'V3.2；19 October 2026；18 页；71 个编号，非 71 个原子字段。'),
    ('群文件来源', 'Futris部门群；孔瑞霞；2026-10-28 11:50:33（Asia/Shanghai）。'),
    ('文件核验', f'已与钉钉大小、MD5 及欧委会官网原文件逐字节核对；{META["sizeBytes"]} bytes。'),
    ('SHA256', META['sha256']),
    ('欧委会发布页', META['officialPage']),
    ('欧委会 PDF', META['officialPdfUrl']),
    ('授权与加工说明', '原文 © European Union, 2026；PDF 第 4 页标 CC BY 4.0。本工作簿为中文解读和实施建议，并非官方译本；原文 PDF 保持完整。'),
    ('阅读顺序', '先看本页与“系统需求”，再筛选“71项解读”；英文及完整适用性文字见“原文矩阵”；边界见“待核验事项”。'),
    ('来源和要求强度', '“Mandatory”等是原指南对应栏文字，不自动等于新的法律条款；应同时核对名称中的条件、法规适用范围与时点。'),
    ('适用范围', '指南分 EV、LMT、工业；第 77(1) 条的护照义务针对每个适用电池，其中工业电池容量门槛为大于 2 kWh。不能把所有小容量电池一概认定不需要护照。'),
    ('数据层级', 'PDF 第 18 页：原则上是型号信息，明确注明单体的除外；51—71 明确标 individual battery。UPI 等标识还须按其法定语义关联。'),
    ('权限说明', '英文访问列忠实保留原文。具体主体资格、访问目的和各类法定权限须按有效法源核对，不能从表中省略字样自行推定其他主体绝无权限。'),
    ('与当前模型关系', '现有 BatteryPass 2.0 的 92 行属性与 71 个指南编号不是一对一；本次为分析，未完成全部 Schema 的逐字段合规审计。'),
    ('已确认映射差异', '旧详细组成 45→新46；旧零件46与备件47→新47；新40为可选放电倍率；旧索引中温度上限指向35应复核为34。'),
    ('与其他文件的分工', '指南用于内容、适用性和访问分类；EN18222用于业务接口方法；EN18239用于授权安全；EN18246用于来源和完整性。具体采用的模型和协议仍须逐项设计。'),
    ('另外核到的官网更新', '截至 2026-10-31，欧委会资源页已列出 2026-10-30 的 Registry API Integration Guide、OpenAPI、语义资源及认证指南。本指南本身不是这些注册接口规范；旧对话中的“官方接口尚未发布”需更新。'),
    ('法规核对链接', 'https://eur-lex.europa.eu/eli/reg/2023/1542/oj/eng'),
    ('分析日期', '2026-10-31；数据点的解释与实施建议应按后续正式文件继续复核。'),
]

wb = Workbook()
wb.remove(wb.active)


def length_units(s):
    return sum(2 if ord(ch) > 255 else 1 for ch in s)


def make_sheet(name, headers, rows, widths, freeze='B2'):
    ws = wb.create_sheet(name)
    ws.append(headers)
    for row in rows: ws.append(row)
    ws.freeze_panes = freeze
    ws.sheet_view.zoomScale = 85
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = '1:1'
    ws.print_options.horizontalCentered = True
    ws.oddFooter.center.text = '指南 V3.2 · 内部分析 · &P / &N'
    for i, width in enumerate(widths, 1): ws.column_dimensions[get_column_letter(i)].width = width
    header_fill = PatternFill('solid', fgColor='173B51')
    for cell in ws[1]:
        cell.font = Font(name='Noto Sans CJK SC', bold=True, color='FFFFFF', size=11)
        cell.fill = header_fill
        cell.alignment = Alignment(vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 36
    for row in ws.iter_rows(min_row=2):
        max_lines = 1
        for j, cell in enumerate(row):
            cell.font = Font(name='Noto Sans CJK SC', size=10, color='22313A')
            cell.alignment = Alignment(vertical='top', wrap_text=True)
            if cell.row % 2 == 0: cell.fill = PatternFill('solid', fgColor='F2F6F8')
            if isinstance(cell.value, str):
                max_lines = max(max_lines, sum(max(1, math.ceil(length_units(line) / max(widths[j] - 3, 1))) for line in cell.value.split('\n')))
                if cell.value.startswith('https://'):
                    cell.hyperlink = cell.value
                    cell.font = Font(name='Noto Sans CJK SC', size=10, color='125D93', underline='single')
        ws.row_dimensions[row[0].row].height = min(390, max(34, 15 * max_lines + 14))
    tab = Table(displayName='Data' + str(len(wb.worksheets)), ref=ws.dimensions)
    tab.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2', showRowStripes=False)
    ws.add_table(tab)
    return ws


make_sheet('阅读说明', ['项目', '说明'], OVERVIEW, [27, 113])
analysis_rows = []
raw_rows = []
for r in DATA:
    n = r['item']
    name, defined, legal, kind, ev, lmt, ind = r['cells']
    meaning, implementation, caution, mapping = DETAILS[n]
    analysis_rows.append([n, LABELS[n-1], applicability(ev), applicability(lmt), applicability(ind), access(n), meaning, implementation, caution or '按原文适用条件及有效法规核对。', legal, '、'.join(map(str, r['pages'])), mapping])
    raw_rows.append([n, name, defined, legal, kind, ev, lmt, ind, '、'.join(map(str, r['pages']))])

ws = make_sheet('71项解读', ['编号', '中文数据项', 'EV 要求', 'LMT 要求', '工业电池要求', '访问与资源层级', '填写与校验含义', '系统实现建议', '边界及待确认点', '法律来源（指南原文）', 'PDF页', '现有模型映射提示'], analysis_rows, [8, 31, 25, 25, 34, 43, 50, 53, 57, 25, 10, 59], 'C2')
for row in ws.iter_rows(min_row=2):
    n = row[0].value
    if n in {2, 7, 16, 25, 34, 40, 45, 46, 47, 51, 61, 62, 63, 64, 65, 66, 67, 70, 71}:
        row[0].fill = PatternFill('solid', fgColor='FFF0C2')
    row[1].hyperlink = SOURCE.name + '#page=' + str(DATA[n-1]['pages'][0])
    row[1].comment = Comment('英文数据项：' + DATA[n-1]['cells'][0], '来源：原文 PDF')

make_sheet('系统需求', ['编号', '建议优先级', '需求点', '实现与验收重点', '相关指南项', '依据性质与边界', '对当前系统的影响'], REQS, [12, 13, 29, 85, 27, 63, 70])
make_sheet('待核验事项', ['编号', '事项', '为什么需要核对', '建议处理'], ISSUES, [10, 30, 108, 76])
make_sheet('原文矩阵', ['Item', 'Data point name', 'As defined in', 'Data point source', 'Type of information', 'EV', 'LMT', 'Industrial', 'PDF page'], raw_rows, [8, 72, 42, 28, 52, 57, 57, 63, 12], 'C2')

wb.properties.creator = 'Codex / internal analysis'
wb.properties.title = '官方指南 V3.2：71 项数据点解读与 DPP 系统需求'
wb.properties.subject = '基于用户指定群文件；非官方中文分析；原文 CC BY 4.0'
wb.properties.description = '原文矩阵 + 中文解释 + 系统实施建议；不等同法规或官方译本。'
out = ROOT / '官方指南V3.2_71项解读与系统需求.xlsx'
wb.save(out)

# 与本次交付直接相关的核对：覆盖、引用、格式、条件和输出完整性。
check = load_workbook(out, data_only=False)
assert check['71项解读'].max_row == 72
assert check['原文矩阵'].max_row == 72
assert [check['71项解读'].cell(i, 1).value for i in range(2, 73)] == list(range(1, 72))
for r in DATA:
    n = r['item']
    for j, text in enumerate(r['cells'], 2): assert check['原文矩阵'].cell(n+1, j).value == text
assert check['71项解读'].freeze_panes == 'C2'
assert check['71项解读'].cell(41, 3).value == '可选'
assert 'Ah' in check['原文矩阵'].cell(63, 2).value
assert 'BMS' in check['71项解读'].cell(63, 5).value
assert not any(ws.merged_cells.ranges for ws in check.worksheets)
assert all(len(ws.tables) == 1 for ws in check.worksheets)
assert SOURCE.stat().st_size == 433954
verification = {
    'workbook': out.name,
    'items': 71,
    'sheets': check.sheetnames,
    'requirements': len(REQS),
    'openIssues': len(ISSUES),
    'originalFileUnchanged': __import__('hashlib').sha256(SOURCE.read_bytes()).hexdigest() == META['sha256'],
    'numberingComplete': True,
    'rawMatrixMatchesStructuredSource': True,
    'mandatoryLiteralCountsOnly': {label: Counter(r['cells'][idx] for r in DATA)['Mandatory'] for idx, label in [(4, 'EV'), (5, 'LMT'), (6, 'Industrial')]},
    'countCaution': '仅统计原文单元格恰为 Mandatory 的数量，不是剔除条件后的实际必填字段数。',
}
(ROOT / '分析工作簿_验证结果.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2))
print(json.dumps(verification, ensure_ascii=False, indent=2))
print('Saved:', out)
