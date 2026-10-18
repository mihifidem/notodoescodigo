def calcular_match_candidato_oferta(user_skills, offer_skills, softskills_result, experiencia_anos):
    """
    Calcula un porcentaje de compatibilidad (0-100) entre un candidato y una oferta.

    Args:
        user_skills (set): Skills del usuario (nombres o ids).
        offer_skills (set): Skills requeridas por la oferta (nombres o ids).
        experiencia_anos (int): Años de experiencia del candidato.

    Returns:
        int: Porcentaje de match (0-100)
    """
    # Peso de cada factor (ajustable)
    PESO_SKILLS = 0.7
    PESO_EXPERIENCIA = 0.3

    # Skills match
    if not offer_skills:
        skills_score = 1.0
    else:
        skills_score = len(user_skills & offer_skills) / len(offer_skills)

    # Experiencia: 5+ años = 1.0, 0 años = 0.0, lineal
    experiencia_score = min(experiencia_anos, 5) / 5

    match = (
        skills_score * PESO_SKILLS +
        experiencia_score * PESO_EXPERIENCIA
    )
    return int(round(match * 100))
