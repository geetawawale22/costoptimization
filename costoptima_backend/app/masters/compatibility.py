
"""Value-free Excel compatibility evidence; never stores master values or formulas."""
from pathlib import Path
from functools import lru_cache
from io import BytesIO
from copy import copy
from hashlib import sha256
from decimal import Decimal,InvalidOperation
from datetime import date,datetime
import gzip,json,xml.etree.ElementTree as ET
from openpyxl.styles.stylesheet import apply_stylesheet
from openpyxl.worksheet.dimensions import ColumnDimension,RowDimension
from openpyxl.xml.functions import fromstring
from zipfile import ZipFile,ZIP_DEFLATED

PROFILE=Path(__file__).with_name('export-format.xml.gz')


def value_digest(value):
    if isinstance(value,(date,datetime)):value=value.isoformat()
    return sha256(json.dumps(value,default=str,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def mapping_digest(fields):
    return value_digest([{k:f.get(k) for k in ('column_key','position','sql_type')} for f in fields])


@lru_cache(maxsize=4)
def _read(path,mtime):
    with gzip.open(path,'rb') as stream:root=ET.fromstring(stream.read())
    return root


def evidence():
    return _read(str(PROFILE),PROFILE.stat().st_mtime_ns) if PROFILE.is_file() else None


class ExportFormat:
    def __init__(self):
        self.root=evidence()
        self.warnings=[]
        self.sources={}
        self.types={}
        self.layouts={}
        self.headers={}
        self.sheets={}
        if self.root is not None:
            for s in self.root.findall('source'):
                name=s.attrib['table'];self.sources[name]=s.attrib
                self.types[name]={(int(c.attrib['row']),c.attrib['field']):c.attrib for c in s.findall('type')}
            for l in self.root.findall('layout'):
                self.layouts[l.attrib['master']]=(l.attrib,{(r.attrib['table'],int(r.attrib['row'])):int(r.attrib['excel_row']) for r in l})
            self.headers={(h.attrib['sheet'],h.attrib['cell']):h.attrib for h in self.root.findall('header')}
            self.sheets={s.attrib['name']:s for s in self.root.findall('sheet')}

    def warn(self,text):
        if text not in self.warnings:self.warnings.append(text)

    def prepare(self,sources):
        valid={}
        for s in sources:
            name=s['data_table_name'];definition=self.sources.get(name)
            valid[name]=bool(definition and mapping_digest(s['columns'])==definition['mapping_sha256'])
            if self.root is not None and not definition and s.get('master_code') in ('assy','norms_consign','norms_pnf'):self.warn(name+': no verified Excel compatibility evidence is available.')
            if definition and not valid[name]:self.warn(name+': Excel type evidence is stale after a field mapping change; source values retained.')
        return valid

    def check_row_evidence(self,sources,rows):
        by_family={s['folder_path']:s['data_table_name'] for s in sources}
        actual={s['data_table_name']:[] for s in sources}
        for row in rows:actual[by_family[row['source_folder']]].append(int(row['source_row_id']))
        for table,identities in actual.items():
            configured=self.sources.get(table,{})
            if configured.get('row_ids_sha256') and value_digest(identities)!=configured['row_ids_sha256']:
                self.warn(table+': source row identities changed; cells without verified type evidence retain their database values.')

    def convert(self,source,row_id,key,value,valid=True):
        rule=self.types.get(source,{}).get((int(row_id),key))
        if not rule:return value
        if not valid or value_digest(value)!=rule['sha256']:
            self.warn(source+': Excel type evidence is stale for a changed cell; its current value is retained.')
            return value
        try:
            if rule['kind']=='number':
                if isinstance(value,str) and len(value)>1 and value.startswith('0') and value[1:2].isdigit():return value
                converted=Decimal(str(value))
                if not converted.is_finite():raise ValueError()
                return converted
            if rule['kind']=='boolean':
                if str(value) not in ('True','False'):raise ValueError()
                return str(value)=='True'
            if rule['kind']=='date':return datetime.fromisoformat(str(value))
        except (InvalidOperation,ValueError,TypeError):
            self.warn(source+': Excel cell type could not be restored safely; source value retained.')
        return value

    def row_positions(self,master,rows,by_family):
        configured=self.layouts.get(master['master_code'])
        if not configured:return None
        attrs,positions=configured
        identities=[(by_family[r['source_folder']]['data_table_name'],int(r['source_row_id'])) for r in rows]
        if value_digest(identities)!=attrs['row_order_sha256']:
            self.warn(master['master_code']+': verified blank-row layout no longer matches current row identities/order; compact layout used.')
            return None
        return [positions[k] for k in identities]

    def header(self,sheet,coordinate,value):
        rule=self.headers.get((sheet,coordinate))
        if rule and value_digest(value)==rule['sha256'] and rule['kind']=='number':return Decimal(str(value))
        return value

    def apply_presentation(self,workbook,decimals):
        if self.root is None:return
        # Read styles only. The profile has no workbook values or executable formulas.
        styles=self.root.find('styles')
        if styles is not None:
            content=BytesIO()
            with ZipFile(content,'w',ZIP_DEFLATED) as archive:archive.writestr('xl/styles.xml',ET.tostring(styles[0],encoding='utf-8'))
            with ZipFile(BytesIO(content.getvalue())) as archive:apply_stylesheet(archive,workbook)
            for sheet in workbook:
                for row in sheet:
                    for cell in row:cell._style=copy(workbook._cell_styles[0])
        for sheet in workbook:
            configured=self.sheets.get(sheet.title)
            if configured is None:continue
            sheet.freeze_panes=configured.attrib.get('freeze') or None
            sheet.column_dimensions.clear();sheet.row_dimensions.clear()
            if configured.find('sheetFormatPr') is not None:
                from openpyxl.worksheet.dimensions import SheetFormatProperties
                sheet.sheet_format=SheetFormatProperties.from_tree(fromstring(ET.tostring(configured.find('sheetFormatPr'))))
            for child in configured:
                if child.tag=='column':
                    dim=ColumnDimension(sheet,**json.loads(child.attrib['attributes']));sheet.column_dimensions[dim.index]=dim
                elif child.tag=='row':
                    attrs=json.loads(child.attrib['attributes']);index=int(attrs.pop('index'))
                    sheet.row_dimensions[index]=RowDimension(sheet,index=index,**attrs)
                elif child.tag=='style':
                    cell=sheet[child.attrib['cell']];cell._style=copy(workbook._cell_styles[int(child.attrib['id'])])
                elif child.tag=='merge':sheet.merge_cells(child.attrib['range'])
            for row in sheet:
                for cell in row:
                    cell.value=self.header(sheet.title,cell.coordinate,cell.value)
                    # Exact and six-place modes retain original number-format evidence.
                    # Numeric cell values still follow the requested precision policy.
