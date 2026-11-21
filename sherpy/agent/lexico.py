"""Adaptación léxica según la edad de los adultos (> 18 años) del hogar.

Solo añade una instrucción de estilo (vocabulario, tono, tecnicismo). No cambia datos, reglas ni formato:
si no hay edades o no hay adultos, devuelve "" y el agente se comporta como siempre.
"""

from __future__ import annotations

from typing import Any

EDAD_ADULTO = 18  # se cuentan solo los miembros con edad > 18

ESTILOS: dict[str, str] = {
    "joven": (
        "Adultos jóvenes (19-30 años): lenguaje cercano, directo, ágil y moderno; explica los conceptos "
        "financieros de forma práctica y sin solemnidad innecesaria."
    ),
    "media": (
        "Adultos de mediana edad (31-55 años): tono profesional, claro y equilibrado, orientado a la toma "
        "de decisiones y a la planificación familiar."
    ),
    "mayor": (
        "Adultos mayores (más de 55 años): vocabulario pausado, respetuoso, muy claro y bien estructurado; "
        "evita anglicismos financieros innecesarios (o tradúcelos con naturalidad) y prioriza la seguridad "
        "y la claridad."
    ),
}


def _household(datos_familia: Any) -> list[dict[str, Any]]:
    """Admite el financial-health entero, su sección `case`, o la lista `household` directamente."""
    if isinstance(datos_familia, list):
        return [m for m in datos_familia if isinstance(m, dict)]
    if isinstance(datos_familia, dict):
        if "household" in datos_familia:
            return _household(datos_familia["household"])
        if isinstance(datos_familia.get("case"), dict):
            return _household(datos_familia["case"])
    return []


def _tramo(edad: float) -> str:
    if edad <= 30:
        return "joven"
    if edad <= 55:
        return "media"
    return "mayor"


def obtener_perfil_lexico_adultos(datos_familia: Any) -> str:
    """Instrucción de estilo según los miembros del hogar con más de 18 años; "" si no hay ninguno."""
    adultos: list[tuple[str, int]] = []
    for miembro in _household(datos_familia):
        try:
            edad = int(miembro.get("age"))
        except (TypeError, ValueError):
            continue
        if edad > EDAD_ADULTO:
            adultos.append((str(miembro.get("first_name") or "adulto"), edad))
    if not adultos:
        return ""

    tramos = sorted({_tramo(e) for _, e in adultos}, key=list(ESTILOS).index)
    quienes = ", ".join(f"{n} ({e})" for n, e in adultos)
    if len(tramos) == 1:
        estilo = ESTILOS[tramos[0]]
    else:
        estilo = (
            "Hay adultos de distintos tramos de edad: combina un tono claro, cercano y accesible para todos a la vez. "
            + " ".join(ESTILOS[t] for t in tramos)
        )
    return (
        f"ESTILO LÉXICO (adultos del hogar: {quienes}). Adapta solo el vocabulario, el tono, el nivel de "
        f"tecnicismo y la forma de explicar; no cambies los datos, las reglas ni el formato que pida el usuario. "
        f"{estilo}"
    )
