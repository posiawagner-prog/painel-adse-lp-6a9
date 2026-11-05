#!/usr/bin/env python3
"""Gera dados.js a partir das planilhas ADSE do 8º e 9º ano (Diagnóstica · Percurso · Saída)."""
from __future__ import annotations

import json
import os
import re
import sys
from decimal import Decimal, ROUND_HALF_UP

try:
    import openpyxl
except ImportError:
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl", "-q"])
    import openpyxl

ROOT = os.path.dirname(os.path.abspath(__file__))
ANOS = (8, 9)
# Escala LP 8º/9º (tabela municipal): 100% → 375 pts (Nível 8)
SCALE_LP = 375.0


def round_half_up(value: float, ndigits: int = 2) -> float:
    d = Decimal(f"{float(value):.{ndigits + 8}f}")
    return float(d.quantize(Decimal(1).scaleb(-ndigits), rounding=ROUND_HALF_UP))


def norm(text: str) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip())
    s = s.replace("C. E.", "C.E.").replace("E. M. E. I. F.", "E.M.E.I.F.")
    return s


def parse_escola_turma(raw: str) -> tuple[str, str]:
    raw = norm(raw)
    match = re.match(r"(.+?)\s*-\s*([A-D])\s*$", raw)
    if match:
        return norm(match.group(1)), match.group(2).upper()
    return raw, "ÚNICA"


def find_sheet(wb, candidates: list[str]) -> str:
    names = {name.strip().lower(): name for name in wb.sheetnames}
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in names:
            return names[key]
    for candidate in candidates:
        key = candidate.strip().lower()
        for sheet_key, sheet_name in names.items():
            if key in sheet_key:
                return sheet_name
    raise KeyError(f"Aba não encontrada: {candidates}")


def _norm_header(value) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().upper())
    return (
        text.replace("Ã", "A")
        .replace("Á", "A")
        .replace("À", "A")
        .replace("Â", "A")
        .replace("É", "E")
        .replace("Ê", "E")
        .replace("Í", "I")
        .replace("Ó", "O")
        .replace("Ô", "O")
        .replace("Ú", "U")
        .replace("Ç", "C")
    )


def find_proficiencia_cols(ws) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for row in range(12, 16):
        for col in range(1, ws.max_column + 1):
            header = _norm_header(ws.cell(row, col).value)
            if not header:
                continue
            if "ACERTO" in header and "pctAcertos" not in mapping:
                mapping["pctAcertos"] = col
            elif "APRENDIZADO" in header and "aprendizado" not in mapping:
                mapping["aprendizado"] = col
            elif "PROFICI" in header and "proficiencia" not in mapping:
                mapping["proficiencia"] = col
            elif header.startswith("NIVEL") and "nivel" not in mapping:
                mapping["nivel"] = col
    return mapping


def parse_nivel(value) -> tuple[int | None, str]:
    raw = re.sub(r"\s+", " ", str(value or "").strip())
    if not raw:
        return None, ""
    match = re.search(r"(\d+)", raw)
    numero = int(match.group(1)) if match else None
    label = (
        raw
        if "NIVEL" in _norm_header(raw) or "NÍVEL" in raw.upper()
        else (f"NÍVEL {numero}" if numero is not None else raw)
    )
    return numero, label


def normalize_aprendizado(value) -> str:
    key = _norm_header(value)
    if "INSUF" in key:
        return "INSUFICIENTE"
    if "BASICO" in key or "BÁSICO" in str(value or "").upper():
        return "BÁSICO"
    if "PROFIC" in key:
        return "PROFICIENTE"
    if "AVANC" in key or "AVANÇ" in str(value or "").upper():
        return "AVANÇADO"
    return str(value or "").strip().upper()


def aprendizado_from_proficiencia(score: float, disciplina: str = "lp") -> str:
    """LP 8º/9º: ≤199 / 200-274 / 275-324 / ≥325."""
    if score <= 199:
        return "INSUFICIENTE"
    if score <= 274:
        return "BÁSICO"
    if score <= 324:
        return "PROFICIENTE"
    return "AVANÇADO"


def nivel_from_proficiencia(score: float, disciplina: str = "lp") -> tuple[int, str]:
    """Faixas LP 8º/9º até Nível 8 (≥375)."""
    thresholds = [
        (375, 8),
        (350, 7),
        (325, 6),
        (300, 5),
        (275, 4),
        (250, 3),
        (225, 2),
        (200, 1),
    ]
    for minimo, numero in thresholds:
        if score >= minimo:
            return numero, f"NÍVEL {numero}"
    return 0, "NÍVEL 0"


def build_proficiencia_block(
    pct_acertos: float | None = None,
    aprendizado: str | None = None,
    proficiencia: float | None = None,
    nivel: int | None = None,
    nivel_label: str = "",
    disciplina: str = "lp",
) -> dict | None:
    scale = SCALE_LP
    pct = float(pct_acertos) if isinstance(pct_acertos, (int, float)) else None
    prof = float(proficiencia) if isinstance(proficiencia, (int, float)) else None
    if pct is None and prof is None:
        return None
    if pct is not None and pct <= 1:
        pct = pct * 100
    if prof is None and pct is not None:
        prof = scale * (pct / 100)
    if pct is None and prof is not None:
        pct = (prof / scale) * 100
    pct = round_half_up(pct, 2)
    prof = round_half_up(prof, 2)
    aprend = normalize_aprendizado(aprendizado) if aprendizado else aprendizado_from_proficiencia(prof, disciplina)
    if nivel is None:
        nivel, nivel_label = nivel_from_proficiencia(prof, disciplina)
    elif not nivel_label:
        nivel_label = f"NÍVEL {nivel}"
    return {
        "pctAcertos": pct,
        "aprendizado": aprend,
        "proficiencia": prof,
        "nivel": nivel,
        "nivelLabel": nivel_label,
    }


def media_from_questoes(questoes: dict[str, float]) -> float:
    if not questoes:
        return 0.0
    return sum(questoes.values()) / len(questoes)


def parse_questions(wb, sheet: str) -> tuple[list[str], dict[str, dict]]:
    ws = wb[sheet]
    is_lp = "portug" in sheet.lower()
    disciplina = "lp"
    questions: list[str] = []
    col = 4
    while col <= ws.max_column:
        header = ws.cell(13, col).value
        if header and "Quest" in str(header):
            questions.append(re.sub(r"\s+", " ", str(header).strip()))
            col += 2
        else:
            col += 1

    prof_cols = find_proficiencia_cols(ws) if is_lp else {}
    schools: dict[str, dict] = {}
    for row in range(14, ws.max_row + 1):
        name = ws.cell(row, 1).value
        flag = ws.cell(row, 2).value
        if not name:
            continue
        escola = norm(str(name))
        flag_text = str(flag or "").strip().upper().replace("Ã", "A").replace("ÃO", "AO")
        if flag_text.startswith("PARTIC"):
            break
        if flag_text in ("SIM", "NAO", "NÃO"):
            if flag_text == "SIM" and escola in schools:
                questoes = {}
                acertos = {}
                for index, question in enumerate(questions):
                    quant = ws.cell(row, 4 + index * 2).value
                    pct = ws.cell(row, 5 + index * 2).value
                    if isinstance(pct, str) and pct.startswith("="):
                        pct = None
                    if isinstance(pct, (int, float)):
                        questoes[question] = float(pct)
                    elif isinstance(quant, (int, float)):
                        base = schools[escola].get("avaliados") or schools[escola].get("previstos") or 0
                        if base:
                            questoes[question] = (float(quant) * 100.0) / float(base)
                    if isinstance(quant, (int, float)):
                        acertos[question] = int(quant)
                schools[escola]["questoes"] = questoes
                schools[escola]["acertos"] = acertos
                block = None
                if prof_cols:
                    pct_raw = ws.cell(row, prof_cols["pctAcertos"]).value if "pctAcertos" in prof_cols else None
                    aprend = ws.cell(row, prof_cols["aprendizado"]).value if "aprendizado" in prof_cols else None
                    prof = ws.cell(row, prof_cols["proficiencia"]).value if "proficiencia" in prof_cols else None
                    nivel_raw = ws.cell(row, prof_cols["nivel"]).value if "nivel" in prof_cols else None
                    if isinstance(aprend, str) and aprend.startswith("="):
                        aprend = None
                    if isinstance(prof, str) and str(prof).startswith("="):
                        prof = None
                    if isinstance(pct_raw, str) and str(pct_raw).startswith("="):
                        pct_raw = None
                    if isinstance(nivel_raw, str) and str(nivel_raw).startswith("="):
                        nivel_raw = None
                    nivel_num, nivel_label = parse_nivel(nivel_raw)
                    block = build_proficiencia_block(
                        pct_acertos=pct_raw if isinstance(pct_raw, (int, float)) else None,
                        aprendizado=aprend if aprend else None,
                        proficiencia=prof if isinstance(prof, (int, float)) else None,
                        nivel=nivel_num,
                        nivel_label=nivel_label,
                        disciplina=disciplina,
                    )
                if block is None and is_lp and questoes:
                    media = media_from_questoes(questoes)
                    if media:
                        block = build_proficiencia_block(pct_acertos=media, disciplina=disciplina)
                if block:
                    schools[escola]["proficiencia"] = block
            continue
        if isinstance(flag, (int, float)) or (isinstance(flag, str) and flag.strip().isdigit()):
            schools[escola] = {
                "previstos": int(ws.cell(row, 2).value or 0),
                "avaliados": int(ws.cell(row, 3).value or 0),
                "questoes": {},
                "acertos": {},
            }
    return questions, schools


def avg_questions(questions: list[str], schools: dict[str, dict]) -> dict[str, float]:
    result: dict[str, float] = {}
    for question in questions:
        weighted = 0.0
        total = 0
        for school in schools.values():
            pct = school.get("questoes", {}).get(question)
            avaliados = school.get("avaliados", 0)
            if pct is None or not avaliados:
                continue
            weighted += float(pct) * avaliados
            total += avaliados
        if total:
            result[question] = round_half_up(weighted / total, 2)
    return result


def sum_acertos(questions: list[str], schools: dict[str, dict]) -> dict[str, int]:
    result: dict[str, int] = {}
    for question in questions:
        total = sum(int(school.get("acertos", {}).get(question, 0) or 0) for school in schools.values())
        if total:
            result[question] = total
    return result


def assign_ranking(items: list[dict], field: str, target: str) -> None:
    ranked = sorted(items, key=lambda item: item[field], reverse=True)
    index = 0
    position = 1
    while index < len(ranked):
        value = round_half_up(ranked[index][field], 2)
        end = index + 1
        while end < len(ranked) and round_half_up(ranked[end][field], 2) == value:
            end += 1
        label = f"{position}º"
        for item in ranked[index:end]:
            item[target] = label
        position += 1
        index = end


def build_proficiencia_rows(escolas: dict[str, dict], disciplina: str) -> list[dict]:
    rows: list[dict] = []
    for key, data in escolas.items():
        block = data.get("proficiencia")
        if not block:
            continue
        escola, turma = parse_escola_turma(key)
        rows.append(
            {
                "disciplina": disciplina,
                "escola": escola,
                "turma": turma,
                "chave": key,
                "previstos": data.get("previstos", 0),
                "avaliados": data.get("avaliados", 0),
                "pctAcertos": block.get("pctAcertos"),
                "aprendizado": block.get("aprendizado") or "",
                "proficiencia": block.get("proficiencia"),
                "nivel": block.get("nivel"),
                "nivelLabel": block.get("nivelLabel") or "",
            }
        )
    rows.sort(
        key=lambda item: (
            item.get("proficiencia") is None,
            -(item.get("proficiencia") or 0),
            item["escola"],
            item["turma"],
        )
    )
    return rows


def build_turmas(escolas_lp: dict[str, dict], escolas_mat: dict[str, dict], ano: int = 8) -> list[dict]:
    keys = sorted(set(escolas_lp) | set(escolas_mat))
    turmas: list[dict] = []
    for key in keys:
        lp = escolas_lp.get(key, {})
        mat = escolas_mat.get(key, {})
        escola, turma = parse_escola_turma(key)
        prev_lp = lp.get("previstos", 0)
        aval_lp = lp.get("avaliados", 0)
        prev_mat = mat.get("previstos", 0)
        aval_mat = mat.get("avaliados", 0)
        if prev_lp or prev_mat:
            previstos = int(round_half_up((prev_lp + prev_mat) / 2, 0)) if prev_mat else prev_lp
        else:
            previstos = 0
        if aval_lp or aval_mat:
            avaliados = int(round_half_up((aval_lp + aval_mat) / 2, 0)) if aval_mat else aval_lp
        else:
            avaliados = 0
        media_lp = round_half_up(media_from_questoes(lp.get("questoes", {})), 2)
        media_mat = round_half_up(media_from_questoes(mat.get("questoes", {})), 2) if mat else 0.0
        media_geral = media_lp if not mat else round_half_up((media_lp + media_mat) / 2, 2)
        row = {
            "ano": ano,
            "escola": escola,
            "turma": turma,
            "previstos": previstos,
            "avaliados": avaliados,
            "participacao": round_half_up(avaliados / previstos * 100, 1) if previstos else 0,
            "mediaLp": media_lp,
            "mediaMat": media_mat,
            "mediaGeral": media_geral,
            "_mediaLp": media_lp,
            "_mediaMat": media_mat,
            "_mediaGeral": media_geral,
            "previstosLp": prev_lp,
            "avaliadosLp": aval_lp,
            "previstosMat": prev_mat,
            "avaliadosMat": aval_mat,
        }
        if lp.get("proficiencia"):
            row["proficienciaLp"] = lp["proficiencia"]
        if mat.get("proficiencia"):
            row["proficienciaMat"] = mat["proficiencia"]
        turmas.append(row)

    assign_ranking(turmas, "mediaGeral", "rankingGeral")
    assign_ranking(turmas, "mediaLp", "rankingLp")
    assign_ranking(turmas, "mediaMat", "rankingMat")
    return turmas


def build_escolas(turmas: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for turma in turmas:
        groups.setdefault(turma["escola"], []).append(turma)

    escolas: list[dict] = []
    for escola, rows in sorted(groups.items(), key=lambda item: item[0]):
        turmas_label = "+".join(sorted({row["turma"] for row in rows if row["turma"] != "ÚNICA"}))
        nome = f"{escola} ({turmas_label})" if turmas_label else escola
        previstos = sum(row["previstos"] for row in rows)
        avaliados = sum(row["avaliados"] for row in rows)
        prev_lp = sum(row["previstosLp"] for row in rows)
        aval_lp = sum(row["avaliadosLp"] for row in rows)
        prev_mat = sum(row["previstosMat"] for row in rows)
        aval_mat = sum(row["avaliadosMat"] for row in rows)
        media_lp = round_half_up(sum(row["_mediaLp"] for row in rows) / len(rows), 2) if rows else 0
        media_mat = round_half_up(sum(row["_mediaMat"] for row in rows) / len(rows), 2) if rows else 0
        media_geral = media_lp if not any(row["_mediaMat"] for row in rows) else round_half_up((media_lp + media_mat) / 2, 2)
        escolas.append(
            {
                "escola": nome,
                "previstos": previstos,
                "avaliados": avaliados,
                "participacao": round_half_up(avaliados / previstos * 100, 1) if previstos else 0,
                "mediaLp": media_lp,
                "mediaMat": media_mat,
                "mediaGeral": media_geral,
                "previstosLp": prev_lp,
                "avaliadosLp": aval_lp,
                "previstosMat": prev_mat,
                "avaliadosMat": aval_mat,
            }
        )

    assign_ranking(escolas, "mediaGeral", "ranking")
    assign_ranking(escolas, "mediaGeral", "rankingGeral")
    assign_ranking(escolas, "mediaLp", "rankingLp")
    assign_ranking(escolas, "mediaMat", "rankingMat")
    return escolas


def finalize_questoes(schools: dict[str, dict]) -> None:
    for school in schools.values():
        school["questoes"] = {key: round_half_up(value, 2) for key, value in school.get("questoes", {}).items()}


def strip_private_fields(turmas: list[dict]) -> None:
    for row in turmas:
        row.pop("_mediaLp", None)
        row.pop("_mediaMat", None)
        row.pop("_mediaGeral", None)


PERIOD_METRIC_KEYS = (
    "previstos",
    "avaliados",
    "participacao",
    "mediaLp",
    "mediaMat",
    "mediaGeral",
    "previstosLp",
    "avaliadosLp",
    "previstosMat",
    "avaliadosMat",
    "rankingGeral",
    "rankingLp",
    "rankingMat",
    "ranking",
    "proficienciaLp",
    "proficienciaMat",
)


PERIOD_LABELS = {
    "diagnostica": "Diagnóstica",
    "percurso": "Percurso",
    "saida": "Saída",
}


def detect_ano(name: str) -> int | None:
    low = name.lower()
    if "backup" in low or "_bak_" in low or name.startswith("~$"):
        return None
    # Preferir marcadores explícitos de ano escolar
    if re.search(r"9\s*[ºo°]?\s*ano", low) or "9º" in name or "9°" in name:
        return 9
    if re.search(r"8\s*[ºo°]?\s*ano", low) or "8º" in name or "8°" in name:
        return 8
    return None


def detect_periodo(name: str, ano: int | None = None) -> str | None:
    low = name.lower()
    if "backup" in low or "_bak_" in low:
        return None
    file_ano = detect_ano(name)
    if file_ano is None or "portug" not in low:
        return None
    if ano is not None and file_ano != ano:
        return None
    if "saida" in low or "saída" in low:
        return "saida"
    if "formativa" in low or "percurso" in low:
        return "percurso"
    if "diagn" in low:
        return "diagnostica"
    return None


def find_xlsx_by_periodo(ano: int) -> dict[str, str]:
    """Retorna {periodo: caminho} das planilhas LP do ano (ignora backups)."""
    found: dict[str, list[str]] = {}
    for name in os.listdir(ROOT):
        if not name.lower().endswith(".xlsx") or name.startswith("~$"):
            continue
        if "_bak_" in name.lower() or "backup" in name.lower():
            continue
        periodo = detect_periodo(name, ano)
        if not periodo:
            continue
        found.setdefault(periodo, []).append(os.path.join(ROOT, name))
    result: dict[str, str] = {}
    for periodo, paths in found.items():
        # Preferir arquivo sem sufixo de backup; entre iguais, o mais recente
        paths.sort(key=lambda p: (-(0 if "_bak_" in os.path.basename(p).lower() else 1), -os.path.getmtime(p)))
        result[periodo] = paths[0]
    return result


def build_periodo(fp: str, periodo: str, ano: int) -> dict:
    wb = openpyxl.load_workbook(fp, data_only=True)
    sheet_lp = find_sheet(wb, ["Português", "Portugues"])
    questoes_lp, escolas_lp = parse_questions(wb, sheet_lp)
    escolas_mat: dict[str, dict] = {}
    questoes_mat: list[str] = []
    try:
        sheet_mat = find_sheet(wb, ["Matemática", "Matematica"])
        questoes_mat, escolas_mat = parse_questions(wb, sheet_mat)
    except KeyError:
        pass

    turmas = build_turmas(escolas_lp, escolas_mat, ano=ano)
    escolas = build_escolas(turmas)
    finalize_questoes(escolas_lp)
    finalize_questoes(escolas_mat)
    strip_private_fields(turmas)

    total_previstos_lp = sum(t["previstosLp"] for t in turmas)
    total_avaliados_lp = sum(t["avaliadosLp"] for t in turmas)
    total_previstos_mat = sum(t["previstosMat"] for t in turmas)
    total_avaliados_mat = sum(t["avaliadosMat"] for t in turmas)
    total_previstos = total_previstos_lp if not total_previstos_mat else int(
        round_half_up((total_previstos_lp + total_previstos_mat) / 2, 0)
    )
    total_avaliados = total_avaliados_lp if not total_avaliados_mat else int(
        round_half_up((total_avaliados_lp + total_avaliados_mat) / 2, 0)
    )
    media_lp = (
        round_half_up(sum(t["mediaLp"] * t["avaliadosLp"] for t in turmas) / total_avaliados_lp, 2)
        if total_avaliados_lp
        else 0
    )
    media_mat = (
        round_half_up(sum(t["mediaMat"] * t["avaliadosMat"] for t in turmas) / total_avaliados_mat, 2)
        if total_avaliados_mat
        else 0
    )
    media_geral = media_lp if not total_avaliados_mat else round_half_up((media_lp + media_mat) / 2, 2)
    proficiencia_lp = build_proficiencia_rows(escolas_lp, "lp")
    proficiencia_mat = build_proficiencia_rows(escolas_mat, "mat")

    return {
        "meta": {
            "titulo": "Avaliação de Desempenho da Secretaria de Educação — ADSE",
            "subtitulo": f"{ano}º Ano · {PERIOD_LABELS.get(periodo, periodo)} · Língua Portuguesa · 2026",
            "fonte": os.path.basename(fp),
            "periodo": periodo,
            "ano": ano,
            "totalPrevistos": total_previstos,
            "totalAvaliados": total_avaliados,
            "previstosLp": total_previstos_lp,
            "avaliadosLp": total_avaliados_lp,
            "previstosMat": total_previstos_mat,
            "avaliadosMat": total_avaliados_mat,
            "participacaoGeral": round_half_up(total_avaliados / total_previstos * 100, 1) if total_previstos else 0,
            "mediaLpGeral": media_lp,
            "mediaMatGeral": media_mat,
            "mediaGeral": media_geral,
            "questoesLp": questoes_lp,
            "questoesMat": questoes_mat,
            "mediaQuestoesLp": avg_questions(questoes_lp, escolas_lp),
            "mediaQuestoesMat": avg_questions(questoes_mat, escolas_mat),
            "acertosQuestoesLp": sum_acertos(questoes_lp, escolas_lp),
            "acertosQuestoesMat": sum_acertos(questoes_mat, escolas_mat),
            "temProficiencia": bool(proficiencia_lp or proficiencia_mat),
            "escalaProficienciaLp": SCALE_LP,
            "disciplinaPadrao": "lp",
        },
        "turmas": turmas,
        "escolas": escolas,
        "escolasQuestoesLp": escolas_lp,
        "escolasQuestoesMat": escolas_mat,
        "proficienciaLp": proficiencia_lp,
        "proficienciaMat": proficiencia_mat,
    }


def metrics_block(row: dict) -> dict:
    return {key: row[key] for key in PERIOD_METRIC_KEYS if key in row}


def merge_turmas(periodos_data: dict[str, dict], ano: int) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}
    for periodo, data in periodos_data.items():
        for row in data["turmas"]:
            key = (row["escola"], row["turma"])
            entry = merged.setdefault(
                key,
                {"ano": row.get("ano", ano), "escola": row["escola"], "turma": row["turma"], "periodos": {}},
            )
            entry["periodos"][periodo] = metrics_block(row)
    return [merged[key] for key in sorted(merged)]


def merge_escolas(periodos_data: dict[str, dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for periodo, data in periodos_data.items():
        for row in data["escolas"]:
            key = row["escola"]
            entry = merged.setdefault(key, {"escola": row["escola"], "periodos": {}})
            entry["periodos"][periodo] = metrics_block(row)
    return [merged[key] for key in sorted(merged)]


def build_ano(ano: int) -> dict | None:
    files = find_xlsx_by_periodo(ano)
    if not files:
        print(f"  (sem planilhas para o {ano}º ano)")
        return None

    order = ["diagnostica", "percurso", "saida"]
    periodos_data: dict[str, dict] = {}
    for periodo in order:
        fp = files.get(periodo)
        if not fp:
            continue
        print(f"  [{ano}º] {periodo}: {os.path.basename(fp)}")
        periodos_data[periodo] = build_periodo(fp, periodo, ano)

    if not periodos_data:
        return None

    periodo_padrao = next((p for p in reversed(order) if p in periodos_data), next(iter(periodos_data)))
    if "saida" in periodos_data:
        periodo_padrao = "saida"

    padrao_data = periodos_data[periodo_padrao]
    by_periodo = {
        periodo: {
            "meta": data["meta"],
            "escolasQuestoesLp": data["escolasQuestoesLp"],
            "escolasQuestoesMat": data["escolasQuestoesMat"],
            "proficienciaLp": data.get("proficienciaLp", []),
            "proficienciaMat": data.get("proficienciaMat", []),
        }
        for periodo, data in periodos_data.items()
    }
    disponiveis = [p for p in order if p in periodos_data]
    labels = " · ".join(PERIOD_LABELS[p] for p in disponiveis)
    meta = {
        **padrao_data["meta"],
        "subtitulo": f"{ano}º Ano · {labels} · Língua Portuguesa · 2026",
        "periodo": periodo_padrao,
        "periodoPadrao": periodo_padrao,
        "periodosDisponiveis": disponiveis,
        "fontes": {periodo: data["meta"]["fonte"] for periodo, data in periodos_data.items()},
    }
    return {
        "meta": meta,
        "byPeriodo": by_periodo,
        "turmas": merge_turmas(periodos_data, ano),
        "escolas": merge_escolas(periodos_data),
        "escolasQuestoesLp": padrao_data["escolasQuestoesLp"],
        "escolasQuestoesMat": padrao_data["escolasQuestoesMat"],
    }


def build() -> dict[str, dict]:
    by_ano: dict[str, dict] = {}
    for ano in ANOS:
        data = build_ano(ano)
        if data:
            by_ano[str(ano)] = data
    if not by_ano:
        raise FileNotFoundError("Nenhuma planilha do 8º ou 9º ano encontrada")
    return by_ano


def main() -> None:
    by_ano = build()
    anos = sorted(int(a) for a in by_ano)
    # Preferir 8º como padrão se existir (painel legado); senão o menor disponível
    ano_padrao = 8 if 8 in anos else anos[0]
    target = os.path.join(ROOT, "dados.js")
    payload = json.dumps(by_ano, ensure_ascii=False, separators=(",", ":"))
    meta = {
        "anosDisponiveis": anos,
        "anoPadrao": ano_padrao,
        "titulo": "Avaliação de Desempenho da Secretaria de Educação — ADSE",
    }
    meta_payload = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(f"const DADOS_POR_ANO = {payload};\n")
        handle.write(f"const DADOS_META = {meta_payload};\n")
        handle.write(
            "let DADOS = DADOS_POR_ANO[String(DADOS_META.anoPadrao)] "
            "|| DADOS_POR_ANO[Object.keys(DADOS_POR_ANO)[0]];\n"
        )
    for ano in anos:
        data = by_ano[str(ano)]
        periodos = ", ".join(data["meta"]["periodosDisponiveis"])
        print(
            f"OK {ano}º ano: {len(data['turmas'])} turmas · {len(data['escolas'])} escolas · {periodos}"
        )
    print(f"Gerado {target} (padrão: {ano_padrao}º ano)")


if __name__ == "__main__":
    main()
