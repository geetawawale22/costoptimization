"""Output_Format placement only; costs are computed in merging.seat."""
from openpyxl.styles import Font,Alignment,PatternFill,Border,Side
from openpyxl.utils import get_column_letter


def seat_output(book,summary,safe_text):
    if not summary.get('enabled'):return None
    ws=book.create_sheet('Output_Format');ws.sheet_view.showGridLines=False
    bases=summary.get('bases',[]);start=3;line=Side(style='thin',color='BFBFBF')
    for block in summary.get('blocks',[]):
        # Original14392-14507 starts the first variant block at C3 and
        # advances by owner-count+3; only this presentation layer uses cells.
        for j,value in enumerate([block['label']]+bases,3):
            cell=ws.cell(start,j);safe_text(cell,value)
            cell.font=Font(name='Calibri',size=8,bold=True);cell.alignment=Alignment(wrap_text=True,horizontal='center',vertical='center')
            cell.fill=PatternFill('solid',fgColor='D9E1F2');cell.border=Border(top=line,bottom=line,left=line,right=line)
        for offset,row in enumerate(block['rows'],1):
            safe_text(ws.cell(start+offset,3),row['End Item'])
            for j,base in enumerate(bases,4):
                cell=ws.cell(start+offset,j);safe_text(cell,row.get('values',{}).get(base))
                cell.font=Font(name='Calibri',size=8);cell.number_format='#,##0.000';cell.alignment=Alignment(wrap_text=True,vertical='center')
                cell.border=Border(top=line,bottom=line,left=line,right=line)
        start+=len(block['rows'])+3
    for j in range(2,max(27,len(bases)+4)):ws.column_dimensions[get_column_letter(j)].width=15
    return ws
