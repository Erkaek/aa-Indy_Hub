import os
import sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'testauth.settings')
sys.path.insert(0, os.getcwd())
import django
print('Python', sys.version)
print('DJANGO_SETTINGS_MODULE', os.environ.get('DJANGO_SETTINGS_MODULE'))
django.setup()
from django.db.models import Sum
from indy_hub.models import MaterialExchangeConfig, MaterialExchangeStock
print('Models import OK')
try:
    from corptools.models import CorpAsset
    has_corptools=True
    print('Corptools import OK')
except Exception as e:
    print('Corptools import failed:', e)
    CorpAsset=None
    has_corptools=False
config = MaterialExchangeConfig.objects.first()
print('CONFIG', None if not config else {
    'corporation_id': config.corporation_id,
    'structure_id': config.structure_id,
    'hangar_division': config.hangar_division,
    'is_active': config.is_active,
})
flag_map={1:'CorpSAG1',2:'CorpSAG2',3:'CorpSAG3',4:'CorpSAG4',5:'CorpSAG5',6:'CorpSAG6',7:'CorpSAG7'}
print('FLAG', flag_map.get(config.hangar_division if config else None))
if has_corptools and config:
    qs = CorpAsset.objects.filter(
        corporation_id=config.corporation_id,
        location_id=config.structure_id,
        location_flag=flag_map.get(config.hangar_division)
    ).values('type_id').annotate(total_qty=Sum('quantity'))
    print('CORPTOOLS COUNT', qs.count())
    print('CORPTOOLS SAMPLE', list(qs[:10]))
else:
    print('NO CORPTOOLS OR CONFIG')
print('STOCK COUNT QTY>0', MaterialExchangeStock.objects.filter(quantity__gt=0).count())
print('STOCK COUNT QTY>0 PRICE>0', MaterialExchangeStock.objects.filter(quantity__gt=0, jita_buy_price__gt=0).count())
print('SAMPLE PRICED STOCK', list(MaterialExchangeStock.objects.filter(quantity__gt=0, jita_buy_price__gt=0).values('type_id','type_name','quantity','jita_buy_price')[:10]))
