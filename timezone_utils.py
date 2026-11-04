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


def data_local_para_utc_inicio(d: date) -> datetime:
    """00:00 do fuso de São Paulo convertido para UTC (início inclusivo de filtro
    de data da UI — created_at é gravado em UTC; comparar a data crua deslocava
    jobs de 21h-23h59 locais para o dia seguinte do filtro)."""
    return datetime.combine(d, datetime.min.time(), tzinfo=TZ_SP).astimezone(timezone.utc)


def data_local_para_utc_fim_exclusivo(d: date) -> datetime:
    """Limite exclusivo de fim de dia do fuso de São Paulo convertido para UTC."""
    return data_local_para_utc_inicio(d) + timedelta(days=1)

