from pydantic import TypeAdapter

def parse[T](cls: type[T], data: object) -> T:
    return TypeAdapter(cls).validate_python(data)
