"""Build the field schema from the data dictionary.

Produces an ordered list of input fields with column letter, unique key,
definition, format, and source guidance — used to (a) build the LLM
extraction prompt and (b) write extracted values back into the template.
"""
from dataclasses import dataclass
from typing import List, Dict
import openpyxl


@dataclass
class Field:
    col: str            # Excel column letter, e.g. "AC"
    key: str            # Unique variable key (disambiguated)
    block: str          # Logical block (for prompt grouping)
    row10_header: str   # Lowest-level header text from template
    definition: str
    fmt: str            # Expected entry / format
    guidance: str
    source: str


def load_fields(dict_path: str) -> List[Field]:
    wb = openpyxl.load_workbook(dict_path, data_only=True)
    ws = wb['Field Dictionary']
    fields: List[Field] = []
    seen_keys: Dict[str, int] = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r[6] != 'Input':
            continue
        col = r[0]
        var = r[12] or f'col_{col}'
        # Disambiguate duplicates by appending row-10 header hint
        if var in seen_keys:
            seen_keys[var] += 1
            hint = (r[5] or '').strip().replace(' ', '_').replace('/', '_').replace('#', 'num').replace('$', 'usd').lower()
            var = f"{var}__{hint}" if hint else f"{var}__{seen_keys[var]}"
        else:
            seen_keys[var] = 1
        fields.append(Field(
            col=col, key=var, block=r[1] or '',
            row10_header=r[5] or '',
            definition=r[13] or '',
            fmt=r[14] or '',
            guidance=r[15] or '',
            source=r[16] or '',
        ))
    return fields


def field_map(fields: List[Field]) -> Dict[str, Field]:
    return {f.key: f for f in fields}


if __name__ == '__main__':
    fields = load_fields('/mnt/user-data/uploads/Peer_Data_Field_Dictionary_v1.xlsx')
    print(f"Loaded {len(fields)} input fields")
    # Show duplicates that got disambiguated
    for f in fields:
        if '__' in f.key:
            print(f"  disambiguated: {f.col} -> {f.key}")
