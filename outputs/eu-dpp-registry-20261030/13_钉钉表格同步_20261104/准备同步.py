"""Project the user-selected workbook onto the existing 19 online columns."""
from pathlib import Path
import csv
import hashlib
import io
import json
import re

from openpyxl import load_workbook

HERE=Path(__file__).resolve().parent
SOURCE=HERE.parent/'12_工程化字段表v4.0/电池护照_工程化字段表_v4.0_公共字段合并版.xlsx'


def clean(value):
    text='' if value is None else str(value)
    return text.replace('\\r\\n','\n').replace('\\n','\n').replace('\\r','\n').replace('\r\n','\n').replace('\r','\n')


def parse_readback(obj):
    assert obj['success'] and not obj['hasMore'] and not obj['truncationReasons']
    csv_text=re.sub(r'(?m)^\[row=\d+\]\s*','',obj['csv'])
    return [[clean(v) for v in row] for row in csv.reader(io.StringIO(csv_text))]


def code_of(text):
    return next((x.strip() for x in clean(text).splitlines()
                 if re.fullmatch(r'[a-z][A-Za-z0-9]*',x.strip()) and x.strip()!='field'),'')


def main():
    before=json.loads((HERE/'目标表_更新前.json').read_text())
    old=parse_readback(before)
    assert len(old)==122 and all(len(r)==19 for r in old)
    expected=['数据大类','数据小类','数据项名称','工程化字段','电池分类','是否必填','披露权限','数据格式',
              'batterPass数据格式','单位','枚举项','是否采集','是否录入多语言','动静态','示例','中文描述',
              '英文描述','责任部门/岗位','XXX型号']
    assert [x.split('\n')[0] for x in old[0]]==expected
    reference=json.loads((HERE.parent/'12_工程化字段表v4.0/参考输入/工程化3.4_读取快照.json').read_text())
    legacy=parse_readback(reference)
    source_bytes=SOURCE.read_bytes()
    (HERE/'Excel_本次同步快照.xlsx').write_bytes(source_bytes)
    ws=load_workbook(io.BytesIO(source_bytes))['工程化v4.0']
    hs={c.value:c.column for c in ws[1]}
    rows=[]
    matched=[]
    source_codes=[]
    for i in range(2,ws.max_row+1):
        def value(name):return clean(ws.cell(i,hs[name]).value)
        code=value('工程化字段名称')
        if not code:continue
        source_codes.append(code)
        old_row=None
        raw=ws.cell(i,hs['3.4源行']).value
        if raw is not None and str(raw).strip():
            number=int(raw)
            assert 2<=number<=len(old)
            source_name=code_of(legacy[number-1][3])
            target_name=code_of(old[number-1][3])
            assert ((source_name and source_name==target_name) or
                    clean(legacy[number-1][3]).split('\n')[0]==old[number-1][3].split('\n')[0]), ('legacy anchor mismatch',code,number)
            old_row=old[number-1]
            matched.append(dict(field=code,oldOnlineRow=number,newOnlineRow=len(rows)+2))
        description=[value('中文描述')]
        for label,column in [
            ('适用条件','适用条件'),('官方JSON路径','官方JSON路径'),
            ('官方语义字段','官方语义字段'),('命名来源','命名来源'),
            ('语义对应状态','语义对应状态'),('核对状态','核对状态'),
            ('工程与核对备注','工程与核对备注'),('语义定义出处','官方定义出处'),
            ('字段ID','字段ID'),
        ]:
            text=value(column)
            if text:description.append('【'+label+'】\n'+text)
        if value('修订前工程名') and value('修订前工程名')!=code:
            description.append('【修订前工程名】\n'+value('修订前工程名'))
        if raw is not None and str(raw).strip():description.append('【3.4源行】\n'+str(int(raw)))
        if old_row and old_row[8]:
            description.append('【原表参考格式】\n“batterPass数据格式”沿用原表对应字段；本次语义格式以“数据格式”列为准。')
        collect={'手动填报':'0','系统获取':'2'}.get(value('采集方式'),value('采集方式'))
        row=[value('数据大类'),value('数据小类'),value('数据项名称'),code,
             value('电池分类'),value('是否必填'),value('披露权限'),value('数据格式'),
             old_row[8] if old_row else '',value('单位'),value('枚举项'),collect,
             value('多语言'),value('动静态'),value('示例'),'\n\n'.join(description),
             value('英文描述'),value('责任部门'),old_row[18] if old_row else '']
        assert len(row)==19
        assert all(not x.startswith('=') for x in row), ('unexpected formula-like value',code)
        rows.append(row)
    assert len(rows)==len(set(source_codes))==100
    data_rows=len(rows)
    # Explicit blank records clear the old tail in the same bounded write.
    rows += [['']*19 for _ in range(len(old)-1-len(rows))]
    with (HERE/'写入数据_A2-S122.csv').open('w',encoding='utf-8',newline='') as file:
        csv.writer(file,lineterminator='\n').writerows(rows)
    expected_rows=[old[0],*rows]
    (HERE/'预期写入矩阵.json').write_text(json.dumps(expected_rows,ensure_ascii=False,indent=2)+'\n')
    changes=sum(a!=b for row_a,row_b in zip(old[1:],rows) for a,b in zip(row_a,row_b))
    plan=dict(nodeId='Qnp9zOoBVBByzZaNClgbvO9xV1DK0g6l',sheetId='s2',sheetTitle='工程化4.0',
              sourceFile=str(SOURCE),sourceSha256=hashlib.sha256(source_bytes).hexdigest(),
              sourceDataRows=data_rows,existingDataRows=len(old)-1,columnCount=19,
              writeRange='A2:S122',preservedHeaderRange='A1:S1',clearedTail='A102:S122',
              changedCells=changes,matchedLegacyFields=len(matched),legacyMatches=matched,
              untouchedExtraColumns=['batterPass数据格式（按对应字段保留）','XXX型号（按对应字段保留）'],
              extraMetadataDestination='中文描述',existingMergedCells=['F16:F17','F22:F23'],
              csvCharacters=len((HERE/'写入数据_A2-S122.csv').read_text()),
              maxCellLength=max(map(len,(x for row in rows for x in row))))
    (HERE/'同步方案.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in plan.items() if k!='legacyMatches'},ensure_ascii=False,indent=2))
    print('Source fields:',source_codes[:8],'...',source_codes[-5:])


if __name__=='__main__':main()
