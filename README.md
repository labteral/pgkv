# pgkv

## Usage
```python
from pgkv import Store


store = Store(
    host='127.0.0.1',
    namespace='pgkv', # database
    username='postgres',
    password='postgres',
    port=5432,
)

# Put
store.put('table_1', 'key_1', 'value_1')

# Put many
store.put('table_1', kv_pairs=[('key_1', 'value_1'), ('key_2', 'value_2')])

# Get
value = store.get('table_1', 'key_1')

# Exists
store.exists('table_1', 'key_1')

# Scan
for value in store.scan(
    'table_1',
    start_key='key_0', # default: None
    stop_key='key_f', # default: None
    # limit=10, # default: None
    # order_by_timestamp=True, # default: False
    # order_by='value', # default: 'key'
    order='desc' # or -1, default: 'asc'
):
    print(value)
```
