"""Static original consumption formulas/conditions, not master data.

Source: original PCM_master Net/Blank/Gross Consumption base and Consumable,
selected by original part1/part1_stamp/make_hvac. Only formula text and condition
syntax are frozen here. Rates, suppliers, material values and allowances remain
in the existing database. Unknown changed formulas/conditions block execution.
Generated once during the approved code port; reference Excel is not read at runtime.
"""
FORMULAS = {('blank_consumption_base', 'Blank Length'): frozenset({'0',
                                                        '{L / Unfold}*{Blank Length Allowance}',
                                                        '{L / Unfold}+{Blank Length Allowance}',
                                                        '{L}+{Blank Length Allowance}',
                                                        '{L}+{Blank Width Allowance}',
                                                        '{W}+{Blank Length Allowance}',
                                                        '{W}+{Blank Width Allowance}'}),
 ('blank_consumption_base', 'Blank Width'): frozenset({'0',
                                                       '{H}+{Blank Width Allowance}',
                                                       '{W / Unfold}*{Blank Width Allowance}',
                                                       '{W / Unfold}+{Blank Width Allowance}'}),
 ('blank_consumption_base', 'Cal Blank Cons'): frozenset({'((({Pipe Dia}+(2*{T}))^2)-({Pipe Dia}*{Pipe '
                                                          'Dia}))*{Blank Length}*{GSM/ Density}*0.785*10^-9',
                                                          '0',
                                                          '{Blank Consumption}',
                                                          '{Blank Length}*1*1*1*10^-3',
                                                          '{Blank Length}*{Blank Width}*10^-6',
                                                          '{Blank Length}*{Blank Width}*{T}*10^-6',
                                                          '{Blank Length}*{Blank Width}*{T}*{GSM/ '
                                                          'Density}*10^-9',
                                                          '{Cal RM cons}',
                                                          '{Cal RM cons}*{Blank Length Allowance}',
                                                          '{Net RM consumption}*{Blank Length Allowance}',
                                                          '{Pipe Dia}^2*{Blank Length}*1*{GSM/ '
                                                          'Density}*(3.14/4)*10^-9'}),
 ('blank_consumption_base', 'Cal Parts Per Blank'): frozenset({'1',
                                                               'ROUNDDOWN({Blank Length}/{L / Unfold},0)',
                                                               'ROUNDDOWN({Blank Length}/{L / '
                                                               'Unfold},0)*ROUNDDOWN({Blank Width}/{W / '
                                                               'Unfold},0)',
                                                               '{No of parts / Blank}'}),
 ('consumable', 'Consumables cost calculations'): frozenset({'{Rate/UOM}*{% wastage}*{Total Surface '
                                                             'Area}*{GSM/ Density}*10^-3',
                                                             "{Rate/UOM}*{B' Surface area}"}),
 ('gross_consumption_base', 'Cal Gross Consumption'): frozenset({'(({Pipe Dia}+ 2*{T})^2-{Pipe '
                                                                 'Dia}^2)*{L}*{GSM/ '
                                                                 'Density}*(3.14/4)*10^-9/{Cal parts / '
                                                                 'Sheet}',
                                                                 '0',
                                                                 '{Actual Gross Consumption}',
                                                                 '{Cal Blank Cons}*{% wastage}',
                                                                 '{Cal Blank Cons}/{Cal Parts Per Blank}',
                                                                 '{Cal Blank Cons}/{No of parts / Blank}',
                                                                 '{L}*{W}*10^-6/{Cal parts / Sheet}',
                                                                 '{L}*{W}*{T}*10^-6/{Cal parts / Sheet}',
                                                                 '{L}*{W}*{T}*{GSM/ Density}*10^-9/{Cal '
                                                                 'parts / Sheet}',
                                                                 '{Pipe Dia}^2*{L}*{GSM/ '
                                                                 'Density}*(3.14/4)*10^-9/{Cal parts / '
                                                                 'Sheet}'}),
 ('gross_consumption_base', 'Cal parts / Sheet'): frozenset({'1',
                                                             'MAX(((INT({W}/{Blank Length})*IF({Blank '
                                                             'Width}>550,0,INT({L}/{Blank '
                                                             'Width})))+(MAX(IF({Blank '
                                                             'Width}>550,0,MAX(INT(1520/{Blank '
                                                             'Width})*INT(({L}-({Blank Width}*(IF({Blank '
                                                             'Width}>550,0,INT({L}/{Blank Width})))))/{Blank '
                                                             'Length}), INT(1520/{Blank '
                                                             'Length})*INT(({L}-({Blank Width}*(IF({Blank '
                                                             'Width}>550,0,INT({L}/{Blank Width})))))/{Blank '
                                                             'Width})))+ MAX(INT(550/{Blank '
                                                             'Width})*INT(({W}-({Blank '
                                                             'Length}*(INT({W}/{Blank Length}))))/{Blank '
                                                             'Length}),INT(550/{Blank '
                                                             'Length})*INT(({W}-({Blank '
                                                             'Length}*(INT({W}/{Blank Length}))))/{Blank '
                                                             'Width}))))),((IF({Blank '
                                                             'Length}>550,0,INT({L}/{Blank '
                                                             'Length}))*INT({W}/{Blank '
                                                             'Width}))+(MAX(IF({Blank '
                                                             'Length}>550,0,MAX(INT({W}/{Blank '
                                                             'Width})*INT(({L}-({Blank Length}*(IF({Blank '
                                                             'Length}>550,0,INT({L}/{Blank '
                                                             'Length})))))/{Blank Length}), INT({W}/{Blank '
                                                             'Length})*INT(({L}-({Blank Length}*(IF({Blank '
                                                             'Length}>550,0,INT({L}/{Blank '
                                                             'Length})))))/{Blank '
                                                             'Width})))+MAX(INT(550/{Blank '
                                                             'Width})*INT(({W}-({Blank '
                                                             'Width}*(INT({W}/{Blank Width}))))/{Blank '
                                                             'Length}),INT(550/{Blank '
                                                             'Length})*INT(({W}-({Blank '
                                                             'Width}*(INT({W}/{Blank Width}))))/{Blank '
                                                             'Width}))))))*{Cal Parts Per Blank}',
                                                             'MAX(((INT({W}/{Blank Length})*IF({Blank '
                                                             'Width}>550,0,INT({L}/{Blank '
                                                             'Width})))+(MAX(IF({Blank '
                                                             'Width}>550,0,MAX(INT(1520/{Blank '
                                                             'Width})*INT(({L}-({Blank Width}*(IF({Blank '
                                                             'Width}>550,0,INT({L}/{Blank Width})))))/{Blank '
                                                             'Length}), INT(1520/{Blank '
                                                             'Length})*INT(({L}-({Blank Width}*(IF({Blank '
                                                             'Width}>550,0,INT({L}/{Blank Width})))))/{Blank '
                                                             'Width})))+ MAX(INT(550/{Blank '
                                                             'Width})*INT(({W}-({Blank '
                                                             'Length}*(INT({W}/{Blank Length}))))/{Blank '
                                                             'Length}),INT(550/{Blank '
                                                             'Length})*INT(({W}-({Blank '
                                                             'Length}*(INT({W}/{Blank Length}))))/{Blank '
                                                             'Width}))))),((IF({Blank '
                                                             'Length}>550,0,INT({L}/{Blank '
                                                             'Length}))*INT({W}/{Blank '
                                                             'Width}))+(MAX(IF({Blank '
                                                             'Length}>550,0,MAX(INT({W}/{Blank '
                                                             'Width})*INT(({L}-({Blank Length}*(IF({Blank '
                                                             'Length}>550,0,INT({L}/{Blank '
                                                             'Length})))))/{Blank Length}), INT({W}/{Blank '
                                                             'Length})*INT(({L}-({Blank Length}*(IF({Blank '
                                                             'Length}>550,0,INT({L}/{Blank '
                                                             'Length})))))/{Blank '
                                                             'Width})))+MAX(INT(550/{Blank '
                                                             'Width})*INT(({W}-({Blank '
                                                             'Width}*(INT({W}/{Blank Width}))))/{Blank '
                                                             'Length}),INT(550/{Blank '
                                                             'Length})*INT(({W}-({Blank '
                                                             'Width}*(INT({W}/{Blank Width}))))/{Blank '
                                                             'Width}))))))*{No of parts / Blank}',
                                                             'MAX(SUM(INT({L}/{Blank Length})*INT({W}/{Blank '
                                                             'Width}),MAX(INT(({L}-INT({L}/{Blank '
                                                             'Length})*{Blank Length})/{Blank '
                                                             'Length})*INT({W}/{Blank '
                                                             'Width}),INT(({L}-INT({L}/{Blank '
                                                             'Length})*{Blank Length})/{Blank '
                                                             'Width})*INT({W}/{Blank '
                                                             'Length})),MAX(INT({L}/{Blank '
                                                             'Length})*INT(({W}-INT({W}/{Blank '
                                                             'Width})*{Blank Width})/{Blank '
                                                             'Width}),INT(({W}-INT({W}/{Blank Width})*{Blank '
                                                             'Width})/{Blank Length})*INT({L}/{Blank '
                                                             'Width}))),SUM(INT({L}/{Blank '
                                                             'Width})*INT({W}/{Blank '
                                                             'Length}),MAX(INT(({L}-INT({L}/{Blank '
                                                             'Width})*{Blank Width})/{Blank '
                                                             'Width})*INT({W}/{Blank '
                                                             'Length}),INT(({L}-INT({L}/{Blank '
                                                             'Width})*{Blank Width})/{Blank '
                                                             'Length})*INT({W}/{Blank '
                                                             'Width})),MAX(INT({L}/{Blank '
                                                             'Width})*INT(({W}-INT({W}/{Blank '
                                                             'Length})*{Blank Length})/{Blank '
                                                             'Length}),INT({L}/{Blank '
                                                             'Length})*INT(({W}-INT({W}/{Blank '
                                                             'Length})*{Blank Length})/{Blank '
                                                             'Width}))))*{Cal Parts Per Blank}',
                                                             'MAX(SUM(INT({L}/{Blank Length})*INT({W}/{Blank '
                                                             'Width}),MAX(INT(({L}-INT({L}/{Blank '
                                                             'Length})*{Blank Length})/{Blank '
                                                             'Length})*INT({W}/{Blank '
                                                             'Width}),INT(({L}-INT({L}/{Blank '
                                                             'Length})*{Blank Length})/{Blank '
                                                             'Width})*INT({W}/{Blank '
                                                             'Length})),MAX(INT({L}/{Blank '
                                                             'Length})*INT(({W}-INT({W}/{Blank '
                                                             'Width})*{Blank Width})/{Blank '
                                                             'Width}),INT(({W}-INT({W}/{Blank Width})*{Blank '
                                                             'Width})/{Blank Length})*INT({L}/{Blank '
                                                             'Width}))),SUM(INT({L}/{Blank '
                                                             'Width})*INT({W}/{Blank '
                                                             'Length}),MAX(INT(({L}-INT({L}/{Blank '
                                                             'Width})*{Blank Width})/{Blank '
                                                             'Width})*INT({W}/{Blank '
                                                             'Length}),INT(({L}-INT({L}/{Blank '
                                                             'Width})*{Blank Width})/{Blank '
                                                             'Length})*INT({W}/{Blank '
                                                             'Width})),MAX(INT({L}/{Blank '
                                                             'Width})*INT(({W}-INT({W}/{Blank '
                                                             'Length})*{Blank Length})/{Blank '
                                                             'Length}),INT({L}/{Blank '
                                                             'Length})*INT(({W}-INT({W}/{Blank '
                                                             'Length})*{Blank Length})/{Blank Width}))))*{No '
                                                             'of parts / Blank}',
                                                             'ROUNDDOWN({L}/{Blank Length}, 0)*{Cal Parts '
                                                             'Per Blank}',
                                                             'ROUNDDOWN({L}/{Blank Length}, 0)*{No of parts '
                                                             '/ Blank}',
                                                             '{Cal Parts Per Blank}',
                                                             '{No of parts / Blank}'}),
 ('gross_consumption_base', 'L'): frozenset({'{L}', '{Blank Length}*{% wastage}', '{Blank Length}'}),
 ('gross_consumption_base', 'Total Surface Area'): frozenset({'(({Blank Length}*{Blank Width})+({T}*{Blank '
                                                              'Length})+({T}*{Blank Width})*2*10^-6/{Cal '
                                                              'parts / Sheet}',
                                                              '(({L}*{W})+({T}*{L})+({T}*{W})*2*10^-6/{Cal '
                                                              'parts / Sheet}',
                                                              '({Pipe Dia}*{L}*3.14*10^-6)/{Cal parts / '
                                                              'Sheet}',
                                                              '{L}*{W}*10^-6/{Cal parts / Sheet}'}),
 ('gross_consumption_base', 'W'): frozenset({'{Blank Width}', '{Blank Width}*{% wastage}', '{W}'}),
 ('net_consumption_base', 'Cal RM cons'): frozenset({'((({Pipe Dia}+(2*{T}))^2)-({Pipe Dia}*{Pipe Dia}))*{L '
                                                     '/ Unfold}*{GSM/ Density}*0.785*10^-9',
                                                     '0',
                                                     '{H}*{thk}*{Fin Length}*(2.78*10^-6)',
                                                     '{L / Unfold}*10^-3',
                                                     '{L / Unfold}*{W / Unfold}*10^-6',
                                                     '{L / Unfold}*{W / Unfold}*{T}*10^-6',
                                                     '{L / Unfold}*{W / Unfold}*{T}*{GSM/ Density}*10^-9',
                                                     '{Net RM consumption}',
                                                     '{Pipe Dia}*{L / Unfold}*{GSM/ Density}*(3.14/4)*10^-9',
                                                     '{thk}*{Input Blank Length}*({H}*2+{Tube Height (Post '
                                                     'Forming)}*2)*(2.78*10^-6)',
                                                     '{thk}*{Input Blank Length}*{Input Blank '
                                                     'Width}*(2.78*10^-6)'})}

CONDITIONS = frozenset({'Actual Gross Consumption=0',
           'Actual Gross Consumption>0',
           'Blank Consumption=0',
           'Blank Consumption>0',
           'Blank Length=0',
           'Blank Length>0',
           'Blank calculation = %',
           'Blank calculation = Size',
           'Blank calculation!=%',
           'Blank calculation=%',
           'Blank calculation=Rod',
           'Blank calculation=Size',
           'Blank calculation=Tube',
           'Cal Blank Cons=0',
           'Cal Blank Cons>0',
           'Cal RM cons=0',
           'Cal RM cons>0',
           'Fin Length>0',
           'Flowtype = Crossflow',
           'Flowtype = Downflow',
           'Gross cal Method = %',
           'Gross cal Method = Blank',
           'Gross cal Method = Nesting',
           'Gross cal Method =%',
           'Gross cal Method =Blank',
           'Gross cal Method =Nesting',
           'Gross cal Method =Nesting HSTH(550 x 1520)',
           'H>0',
           'Input Blank Length>0',
           'Input Blank Width>0',
           'L / Unfold=0',
           'L / Unfold>0',
           'L>0',
           'Material Code != SMALTB0003',
           'Material Code = SMALTB0001',
           'Material Code = SMALTB0002',
           'Material Code = SMALTB0003',
           'Material Code = SMALTB0004',
           'Material Family!=Rod',
           'Material Family!=Tube',
           'Material Family=Rod',
           'Material Family=Tube',
           'Net RM consumption=0',
           'Net RM consumption>0',
           'No of parts / Blank=0',
           'No of parts / Blank>0',
           'Pipe Dia>0',
           'Tube Height (Post Forming)>0',
           'UOM = Kg',
           'UOM = SQM',
           'UOM = SQM per mm',
           'UOM!=L MTR',
           'UOM=Kg',
           'UOM=L MTR',
           'UOM=SQM',
           'UOM=SQM per mm',
           'W>0',
           'thk>0'})
