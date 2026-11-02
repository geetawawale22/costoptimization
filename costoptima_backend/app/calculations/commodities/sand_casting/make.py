"""PCM sand_casting_receipe:6788-7082 and supplier result:7324-7519.

The charge, rejection, moulding, core and machining graphs use named fields.
Material density and all prices come from the existing relational masters.
Original dimensional conversions and business-loss expressions remain static.
No original workbook is opened by this implementation.
"""
from decimal import Decimal
from ...common import CalculationError,required,number,get,empty,normalized,divide,ratio
from ...masters import FieldRef,MasterLookupError
from .buy_non_std import calculate_range
SOURCE={'function':'sand_casting_receipe;sand_casting','lines':'6788-7082;7324-7519'}
ZERO=Decimal(0)
ONE=Decimal(1)
# Verified Material Master period behind the original auxiliary rate references.
AUXILIARY_RATE=FieldRef('Rate/UOM','APR#22')


def recipe_inputs(record,context):
    """Original input-order supplier/grade projection, before set-based outputs.

    MAKE and BUY NON STD enter the recipe projection; other commodities and
    PROCESS rows do not. The first projected input owns the shared RM period
    and the original shared HPML/ARPA labour-rate selection.
    """
    return [row for row in context.rows
            if normalized(get(row,'Commodity'))=='sand casting'
            and normalized(get(row,'Type')) in {'make','buy non std'}
            and not empty(get(row,'Material Grade'))]


def _period(record,context):
    rows=recipe_inputs(record,context)
    owner=next(iter(rows),record)
    return str(required(owner,'RM Base'))


def _ingredient_rate(context,label,period):
    # Source6870/6887 maps grade to the last code, then source6955 prices the
    # first matching code. Preserve both separate duplicate policies.
    ingredient=context.master.record('material_master',{'Material Grade':label},duplicate_policy='last')
    code=ingredient.value('Material Code')
    return code,context.require_master('material_master',{'Material Code':code},
                                      FieldRef('Rate/UOM',period),blank_zero=True)


def calculate_recipe_charge(record,context):
    supplier=required(record,'Supplier');grade=required(record,'Material Grade')
    ref=FieldRef(str(grade),str(supplier));period=_period(record,context)
    charge=rm=consumables=ZERO;phase='charge';ingredients=[]
    recipe_rows=context.master.records('casting_recipie_master')
    selected=[]
    for candidate in recipe_inputs(record,context) or [record]:
        candidate_ref=FieldRef(str(required(candidate,'Material Grade')),str(required(candidate,'Supplier')))
        if candidate_ref not in selected:selected.append(candidate_ref)
    output_rows=[str(row.value('Addition in kg') or '').strip() for row in recipe_rows
                 if any(not empty(row.value(field)) for field in selected)]
    for row in recipe_rows:
        label=str(row.value('Addition in kg') or '').strip()
        if label=='Total':continue
        if label=='Sub Total':phase='consumables';continue
        if label=='Grand Total':break
        quantity=row.value(ref)
        if empty(quantity):continue
        quantity=number(quantity,label+' addition')
        code,rate=_ingredient_rate(context,label,period)
        cost=rate*quantity
        if phase=='charge':charge+=quantity;rm+=cost
        else:consumables+=cost
        ingredients.append({'Material Grade':label,'Material Code':code,'Addition in kg':quantity,
                            'Rate/UOM':rate,'Cost':cost,'phase':phase})
    if charge==0:raise CalculationError('casting_charge_empty','No nonzero verified recipe charge is available for this supplier and casting grade')
    electricity=context.require_master('norms_burning_loss',{'Supplier':supplier},'Electricity for melting Cost/kg')
    burning=context.require_master('norms_burning_loss',{'Supplier':supplier},'Burning Loss')
    power=electricity*charge;subtotal=rm+consumables+power
    return {'Total Charge Kg':charge,'Total RM cost Rs/Charge':rm,'Electricity Cost for melting':electricity,
        'Power cost':power,'Cost Of Consumable':consumables,'Sub Total Cost':subtotal,
        'Burning Loss':burning,'Total with burning loss cost':subtotal*(ONE+burning),
        '_recipe_rm_period':period,'_recipe_ingredients':ingredients,'_recipe_output_rows':output_rows}


def recipe_scrap_rate(record,context):
    """Translate the original shared scrap-reference slot only when proven.

    The original retained-row projection is the union of selected recipe
    columns. Its second retained ingredient must be C.I Return / RR, preceded
    only by Pig Iron, for the documented semantic translation to hold. A
    different retained sequence blocks this affected branch; it is never used
    as a positional master lookup or replaced with an arbitrary scrap price.
    """
    selected=[]
    for row in recipe_inputs(record,context):
        ref=FieldRef(str(required(row,'Material Grade')),str(required(row,'Supplier')))
        if ref not in selected:selected.append(ref)
    if not selected:
        raise CalculationError('casting_scrap_reference_unresolved','Recipe scrap reference needs the original selected supplier/grade projection')
    preceding=[]
    for row in context.master.records('casting_recipie_master'):
        if not any(not empty(row.value(ref)) for ref in selected):continue
        label=str(row.value('Addition in kg') or '').strip()
        if label=='C.I Return / RR':
            if preceding!=['Pig Iron']:
                break
            _,rate=_ingredient_rate(context,'C.I Return / RR',_period(record,context))
            context.provenance.append({'master':'casting_recipie_master',
                'field':'C.I Return / RR','selection':'verified shared scrap ingredient',
                'selected_supplier_grades':[{'supplier':x.group,'material_grade':x.name} for x in selected]})
            return rate
        preceding.append(label)
        if len(preceding)>1:break
    raise CalculationError('casting_scrap_reference_unresolved',
        'Selected recipe rows do not prove that the original shared scrap reference means C.I Return / RR',
        retained_prefix=preceding)


def _linked_number(record,field):
    marker=object();raw=get(record,field,marker)
    if raw is marker:raise CalculationError('missing_input',f'Required input {field} is missing',field)
    return ZERO if empty(raw) else number(raw,field)


def _density(context,keys,label):
    # Existing Material Master GSM/ Density uses g/cm3 in the legacy density
    # formula convention; convert to kg/mm3. A blank property cannot be the
    # old embedded physical density constant or a zero fallback.
    try:
        density=context.require_master('material_master',keys,'GSM/ Density')
    except (CalculationError,MasterLookupError) as error:
        raise CalculationError('casting_density_missing',
            f'{label} needs a verified Material Master GSM/ Density value',
            'GSM/ Density',master='material_master',lookup_keys=keys,master_field='GSM/ Density',
            material_keys=keys,required_units='g/cm3',
            master_error=error.as_dict() if hasattr(error,'as_dict') else {'code':getattr(error,'code','master_lookup_error'),'message':str(error)}) from error
    if density<=0:raise CalculationError('casting_density_invalid',f'{label} density must be positive','GSM/ Density',
        master='material_master',lookup_keys=keys,master_field='GSM/ Density',actual_value=density,required_units='g/cm3')
    return density/Decimal(1000000)


def _auxiliary_rate(context,grade):
    return context.require_master('material_master',{'Material Grade':grade},AUXILIARY_RATE,blank_zero=True)


def calculate_casting_rate(record,context,charge=None):
    """Complete source7005-7082 named-value rate per kg before machining."""
    result=dict(charge if charge is not None else calculate_recipe_charge(record,context))
    try:
        supplier=required(record,'Supplier');molding=str(required(record,'Molding Type')).strip()
        complexity=str(required(record,'Complexity Group')).strip()
        scrap=recipe_scrap_rate(record,context)
        box_yield=ratio(required(record,'Box Yield (%)'),'Box Yield (%)')
        total_charge=result['Total Charge Kg'];subtotal=result['Sub Total Cost']
        good=box_yield*total_charge;risers=total_charge-good
        pre=context.require_master('casting_complexity_master',
            {'Rejection Category':'Casting ( Pre Machining )','Type':molding},FieldRef(complexity,'Rejection %'))
        rejected=good*pre
        rejection_cost=divide(subtotal,total_charge,'Charge cost per kg')*rejected
        rejection_credit=rejected*scrap;credit=risers*scrap
        after=result['Total with burning loss cost']+rejection_cost-rejection_credit-credit
        good_metal=good-rejected;good_metal_rate=divide(after,good_metal,'Good metal cost per kg')
        result.update({'Box Yield (%)':box_yield,'Good Casting ( Kg )':good,'Runners & Risers ( Kg )':risers,
            'Scrap Rate':scrap,'Credit Of Scrap':credit,'Casting Rejection ( % )':pre,
            'Casting Rejection (Kg)':rejected,'Rejection Credit':rejection_cost,
            'Casting stage Rejection Credit':rejection_credit,'Total Cost after Credit and Rejection Cost':after,
            'Good Metal ( Kg )':good_metal,'Good Metal Cost Rs/Kg':good_metal_rate})
        iron_density=_density(context,{'Material Code':required(record,'Material Code')},'Casting iron')
        sand_density=_density(context,{'Material Grade':'Green Sand Cost'},'Green sand')
        gross=_linked_number(record,'Gross Wt');cavity=_linked_number(record,'Cavity')
        cores=_linked_number(record,'No. of Cores');core_weight=_linked_number(record,'Core Weight per Part')
        box_volume=(_linked_number(record,'Moulding Box Length')*_linked_number(record,'Moulding Box Width')*
                    _linked_number(record,'Moulding Box Height'))
        good_box=gross*cavity;outer_volume=divide(good_box,iron_density,'Outer volume of component')
        sand_volume=box_volume-outer_volume;poured=divide(good_box,box_yield,'Poured metal per box')
        runner_weight=poured-good_box;runner_volume=divide(runner_weight,iron_density,'Runner volume')
        runner_sand=runner_volume*sand_density
        green_sand=sand_volume*sand_density-runner_sand-cores*core_weight*cavity
        sand_metal=divide(green_sand+cores*core_weight*cavity,poured,'Sand to metal ratio')
        # Preserve the actual original percentage operators, including the
        # second percentage application at source7043. These are loss-formula
        # coefficients, not substituted prices or material-density defaults.
        wastage=Decimal('.05');recovery=ONE-wastage
        wasted=green_sand*wastage
        net_sand=wasted+green_sand*(ONE-recovery/100)
        recycle=Decimal('.95');fresh=ONE-recycle;fresh_weight=net_sand*fresh
        fresh_per_kg=divide(fresh_weight,good_box,'Fresh sand per casting kg')
        result.update({'Total Volume of Box':box_volume,'Density of Iron (kg/mm3)':iron_density,
            'Outer volume of component (mm3)':outer_volume,'Total volume of sand  (mm3)':sand_volume,
            'Good Casting per Box in Kg':good_box,'Poured Metal per Box':poured,
            'Run & Risers Wt per Box':runner_weight,'Volume of Runner & riser per Box':runner_volume,
            'Weight of Green Sand not used for Runner Riser':runner_sand,'Density of Core sand':sand_density,
            'Weight of green sand/box':green_sand,'Actual Sand to Metal Ratio':sand_metal,
            'Sand wastage':wastage,'Weight of green sand wasted':wasted,
            'Sand recovery - Green sand':recovery,'Net Green sand weight consumed per Mould':net_sand,
            'Recycle Sand':recycle,'Fresh Sand Addition':fresh,'Fresh Sand Addition/ MOLD':fresh_weight,
            'Fresh Sand Addition /KG OF CASTING':fresh_per_kg})
        rates={name:_auxiliary_rate(context,name) for name in
            ['Green Sand Cost','Bentonite Rate','Lustron Rate','Ceramic Filter Rate','Chaplet Rate','Mold Coat Rate']}
        bentonite=context.require_master('norms_burning_loss',{'Supplier':supplier},'Bentonite')
        lustron=context.require_master('norms_burning_loss',{'Supplier':supplier},'Lustron')
        sand_cost=fresh_weight*rates['Green Sand Cost'];sand_per_kg=divide(sand_cost,good_box,'Sand cost per kg')
        bentonite_cost=divide(net_sand*bentonite*rates['Bentonite Rate'],good_box,'Bentonite cost per kg')
        lustron_cost=divide(net_sand*lustron*rates['Lustron Rate'],good_box,'Lustron cost per kg')
        filter_cost=divide(rates['Ceramic Filter Rate'],good_box,'Ceramic filter cost per kg')
        chaplet_cost=ZERO if core_weight==0 else divide(rates['Chaplet Rate'],good_box,'Chaplet cost per kg')
        coat_cost=ZERO if normalized(molding)=='arpa' else divide(rates['Mold Coat Rate'],good_box,'Mold coat cost per kg')
        additives=bentonite_cost+lustron_cost+filter_cost+chaplet_cost+coat_cost
        moulding_sand=additives+sand_per_kg
        core_type=get(record,'Type of Core',get(record,'Type Of Core'))
        core_rate=ZERO if empty(core_type) else _auxiliary_rate(context,str(core_type).strip())
        core_part=core_rate*core_weight;core_cost=divide(core_part,gross,'Core cost per kg')
        lining=_auxiliary_rate(context,'Furnace and Laddle Lining Cost /Kg')
        # Source7072-7074 refer to the first recipe input's molding type for
        # every row. Preserve this shared selector rather than per-row guesses.
        first=next(iter(recipe_inputs(record,context)),record)
        rate_group='HPML' if normalized(required(first,'Molding Type'))=='hpml' else 'ARPA'
        labour=context.require_master('norms_casting',{'Pericular':'Labour Cost'},rate_group)
        shot=context.require_master('norms_casting',{'Pericular':'Shot Blasting ( Rs/Kg )'},rate_group)
        fettling=context.require_master('norms_casting',{'Pericular':'Fettling Cost ( Rs/Kg )'},rate_group)
        total_core=labour+core_cost
        before_post=total_core+fettling+shot+moulding_sand+good_metal_rate+lining
        post=context.require_master('casting_complexity_master',
            {'Rejection Category':'Casting ( Post Machining  )','Type':molding},FieldRef(complexity,'Rejection %'))
        post_cost=post*before_post;post_credit=post*scrap
        final=before_post+post_cost-post_credit
        result.update(rates)
        result.update({'Sand Cost Per Mould':sand_cost,'Sand Cost Per Kg':sand_per_kg,
            'Bentonite Cost /Kg casting':bentonite_cost,'Lustron Cost /Kg casting':lustron_cost,
            'Ceramic Filter Cost /Kg casting':filter_cost,'Chaplet Cost /Kg casting':chaplet_cost,
            'Mold Coat Cost /Kg casting':coat_cost,'Total Sand Additives':additives,
            'Moulding Sand Cost / Kg':moulding_sand,'Type of Core':core_type,
            'No. of Cores':cores,'Core Weight per Part':core_weight,
            'Core Sand Rate/Kg':core_rate,'Core Cost Per Part':core_part,
            'Furnace and Laddle Lining Cost /Kg':lining,'Labour Cost':labour,
            'Shot Blasting ( Rs/Kg )':shot,'Fettling Cost ( Rs/Kg )':fettling,
            'Core Cost':core_cost,'Total Cost / Kg':total_core,
            'Moulding + Core  - Total RM Cost / Kg':before_post,
            'Casting Rejection ( % ) ( Post Machining )':post,
            'Moulding + Core  - Total Rejection Cost / Kg':post_cost,
            'Rejection Credit-HPML + Shell Core Box Process - Total Cost / Kg':post_credit,
            'Total Cost/Kg':final,'RATE/UOM':final,'_casting_norms_molding_group':rate_group})
        return result
    except CalculationError as error:
        error.details.setdefault('partial_values',dict(result))
        raise


def calculate(record,context):
    values=calculate_recipe_charge(record,context)
    # Preserve the existing explicit block before asking for additional fields
    # when a direct caller has no original recipe projection context.
    try:
        recipe_scrap_rate(record,context)
        values=calculate_casting_rate(record,context,values)
        final=calculate_range(record,context,derived_material_rate=values['RATE/UOM'])
        return {**values,**final,'_casting_rate_complete':True}
    except CalculationError as error:
        error.details.setdefault('partial_values',dict(values))
        raise
