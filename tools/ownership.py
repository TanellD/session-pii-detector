"""Общие правила принадлежности атрибута людям."""

def owner_ids(value):
    if value is None:
        return ()
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("Нужен непустой ID владельца")
        return (value,)
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("Владельцы: строка, непустой список ID или null")
    if any(not isinstance(p, str) or not p.strip() for p in value):
        raise ValueError("Каждый владелец должен иметь непустой строковый ID")
    if len(set(value)) != len(value):
        raise ValueError("Владельцы не должны повторяться")
    return tuple(value)
