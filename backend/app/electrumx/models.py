from pydantic import BaseModel


class ElectrumXRequest(BaseModel):
    method: str
    params: list = []


class ElectrumXResponse(BaseModel):
    result: object | None = None
    error: object | None = None
