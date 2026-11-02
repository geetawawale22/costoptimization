from decimal import Decimal,InvalidOperation
from datetime import datetime,date
ERRORS={'#REF!','#N/A','#DIV/0!','#VALUE!','#NAME?','#NULL!','#NUM!'}


def clean(value):
    if value is None or (isinstance(value,str) and (not value.strip() or value.strip() in ERRORS)):return None
    if isinstance(value,(datetime,date)):return value.isoformat()
    return value


def validate_row(source,values,partial=False):
    fields={f['column_key']:f for f in source['columns'] if f['column_key']!='row_id'}
    errors=[];result={}
    for key in set(values)-set(fields):errors.append({'column':key,'message':'Unknown or immutable column'})
    for key,field in fields.items():
        if key not in values and partial:continue
        value=clean(values.get(key))
        if value is not None:
            try:
                if field['sql_type']=='DECIMAL(20,6)':
                    value=Decimal(str(value))
                    if not value.is_finite() or abs(value)>=Decimal('100000000000000') or value!=value.quantize(Decimal('.000001')):raise ValueError('Expected DECIMAL(20,6) without precision loss')
                else:
                    if isinstance(value,float) and value.is_integer():value=str(int(value))
                    else:value=str(value)
                    if field['sql_type']=='VARCHAR(255)' and len(value)>255:raise ValueError('Maximum 255 characters')
                    if len(value)>65535:raise ValueError('Cell exceeds text limit')
                result[key]=value
            except (ValueError,InvalidOperation):errors.append({'column':key,'message':'Invalid '+field['sql_type']+' value (length, precision or format)'})
        else:result[key]=None
    return result,errors
