from pgkv import Store

store = Store(
    host='10.0.0.102',
    namespace='pgkv',
    username='postgres',
    password='postgres',
    port=5432,
)

table = 'table_10'

store.begin()

store.put(
    table,
    kv_pairs=[
        ('key_1', 'value_1'),
        ('key_5', 'value_5'),
        ('key_3', 'value_3'),
    ],
)
# store.rollback()

store.put(table, 'key_4', 'value_4')
store.put(table, 'key_2', 'value_2')
store.commit()

store.delete(table, 'key_3')

# result = store.get(table, 'key_3')

# store.put(table, 'key_2', '2')

# result = store.get(table, 'key_1', column='cf_6')

start_key = 'key_1'
stop_key = 'key_5'

for result in store.scan(
        table,
        start_key=start_key,
        stop_key=stop_key,
        limit=None,
        order_by_timestamp=False,
        order=1,
        full_row=True,
):
    print(result)

for result in store.scan(table):
    key = result['key']
    print(f'Deleting key: {key}...')
    store.delete(table, result['key'])
