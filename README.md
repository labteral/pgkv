# pgkv

## Usage
```python
from pgkv import Store


store = Store(
    host='127.0.0.1',
    namespace='pgkv', # database
    user='postgres',
    password='postgres',
    port=5432,
)

# Put
store.put('table_1', 'key_1', 'value_1')

# Get
value = store.get('table_1', 'key_1')

# Scan
for value in store.scan(
    'table_1',
    start_key='key_0',
    stop_key='key_f',
    limit=10,
    order='desc'
):
    print(value)

```
