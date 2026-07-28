from datetime import datetime, date, timezone, timedelta

# Local timezone for Brazil/Sao_Paulo (UTC-3)
TZ_SP = timezone(timedelta(hours=-3))

def localize_datetime(dt):
    """
    Converte um objeto datetime (com ou sem fuso horário) para o fuso horário de Brasília (UTC-3).
    Se receber um objeto date simples (sem horário), retorna o próprio objeto date intacto.
    """
    if dt is None:
        return None
    # Apenas objetos datetime (e não date puro) possuem o atributo tzinfo
    if isinstance(dt, datetime):
        if dt.tzinfo is not None:
            return dt.astimezone(TZ_SP)
        return dt.replace(tzinfo=timezone.utc).astimezone(TZ_SP)
    return dt

