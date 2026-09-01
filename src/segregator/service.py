"""
src/segregator/service.py
Связующий сервисный слой (Service Layer API) платформы Segregator.
Объединяет SQLite базу данных, хранилище blobs, мультиагентный граф,
раскладку файлов в дерево archiwum/ и экспорт XLSX-реестров.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import xlsxwriter

from src.segregator.domain.models import (
    DataSource,
    DocumentFacts,
    ExtractedField,
    BookingProposal,
    TaxpayerProfile,
    TaxRegime,
    SyncState,
)
from src.segregator.orchestrator.state import AccountingGraphState
from src.segregator.orchestrator.graph import build_accounting_graph
from src.segregator.db.migrate import get_connection, migrate


class MonthNames:
    """Польские названия месяцев для файлового дерева."""
    PL_MONTHS = {
        1: "01-styczen",
        2: "02-luty",
        3: "03-marzec",
        4: "04-kwiecien",
        5: "05-maj",
        6: "06-czerwiec",
        7: "07-lipiec",
        8: "08-sierpien",
        9: "09-wrzesien",
        10: "10-pazdziernik",
        11: "11-listopad",
        12: "12-grudzien",
    }

    @classmethod
    def get_month_folder(cls, month: int) -> str:
        return cls.PL_MONTHS.get(month, f"{month:02d}-miesiac")


class SegregatorService:
    """
    Главный фасад бизнес-сервисов Segregator.
    """

    def __init__(self, workspace_root: Path, db_path: Optional[Path] = None):
        self.root = workspace_root
        self.db_path = db_path or (workspace_root / "segregator.db")
        self.blobs_dir = workspace_root / "blobs"
        self.archive_dir = workspace_root / "archiwum" / "wg-daty-dokumentu"
        self.registers_dir = workspace_root / "rejestry"
        self.graph = build_accounting_graph()
        
        # Автоматическое создание необходимых каталогов
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.registers_dir.mkdir(parents=True, exist_ok=True)
        
        # Применение миграций схемы
        migrate(self.db_path)

    def _calc_sha256(self, content_bytes: bytes) -> str:
        return hashlib.sha256(content_bytes).hexdigest()

    def store_blob(self, content_bytes: bytes, original_filename: str = "") -> Tuple[str, Path]:
        """
        Сохраняет контент в content-addressed хранилище blobs/ab/cd/hash.ext.
        """
        sha = self._calc_sha256(content_bytes)
        prefix_a = sha[:2]
        prefix_b = sha[2:4]
        target_dir = self.blobs_dir / prefix_a / prefix_b
        target_dir.mkdir(parents=True, exist_ok=True)
        
        ext = Path(original_filename).suffix.lower() if original_filename else ".bin"
        if not ext:
            ext = ".bin"
            
        target_file = target_dir / f"{sha}{ext}"
        if not target_file.exists():
            target_file.write_bytes(content_bytes)
            
        # Запись в таблицу blobs
        conn = get_connection(self.db_path)
        try:
            rel_path = target_file.relative_to(self.root).as_posix()
            conn.execute(
                """
                INSERT OR IGNORE INTO blobs (sha256, bytes, mime, stored_path, ocr_state)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sha, len(content_bytes), "application/octet-stream", rel_path, "processed")
            )
            conn.commit()
        finally:
            conn.close()

        return sha, target_file

    def get_sync_state(self, nip: str) -> SyncState:
        """Считывает водяные знаки синхронизации для налогоплательщика."""
        conn = get_connection(self.db_path)
        try:
            # Считаем синхронизированными те хэши, по которым уже есть проводка в kpir_entries
            rows = conn.execute(
                """
                SELECT b.sha256 
                FROM blobs b
                JOIN attachments a ON a.sha256 = b.sha256
                JOIN documents d ON d.attachment_id = a.id
                JOIN kpir_entries k ON k.document_id = d.id
                """
            ).fetchall()
            synced_hashes = [r[0] for r in rows]
            
            watermark_row = conn.execute(
                "SELECT ksef_last_sync, bank_last_sync, telegram_last_msg_id FROM sync_watermarks WHERE nip = ?",
                (nip,)
            ).fetchone()
            
            last_msg = watermark_row[2] if watermark_row else None
            return SyncState(
                nip=nip,
                telegram_last_message_id=last_msg,
                synced_sha256_hashes=synced_hashes
            )
        finally:
            conn.close()

    def process_document(
        self,
        file_path: Path,
        profile: TaxpayerProfile,
        custom_facts: Optional[DocumentFacts] = None
    ) -> AccountingGraphState:
        """
        Сквозной процесс обработки документа:
        1. Хэширование и сохранение в blobs/
        2. Прогон через мультиагентный граф (Step 0 -> Agent 01 -> Agent 02 -> Agent 03 -> Human Gate)
        3. Запись в SQLite (documents, kpir_entries, zus_declarations, tax_advances)
        4. Раскладка физического файла в archiwum/
        """
        content = file_path.read_bytes()
        sha, blob_file = self.store_blob(content, file_path.name)
        
        sync_state = self.get_sync_state(profile.nip)
        
        # Подготовка состояния графа
        target_doc_date = (custom_facts.doc_date.value if custom_facts and custom_facts.doc_date and isinstance(custom_facts.doc_date.value, date) else date.today())
        initial_state = AccountingGraphState(
            raw_input=sha,
            target_date=target_doc_date,
            taxpayer_profile=profile,
            sync_state=sync_state,
            facts=custom_facts
        )

        # Вызов оркестратора
        final_state = self.graph.invoke(initial_state)

        # Если операция не была пропущена как холостая — сохраняем результаты в базу
        if not final_state.is_delta_empty and final_state.facts:
            self._save_results_to_db(sha, file_path.name, blob_file, final_state, profile)
            
            # Раскладка в дерево архива
            if final_state.kpir_entry:
                self._route_to_archive(file_path, final_state)

        return final_state

    def _save_results_to_db(
        self,
        sha: str,
        orig_name: str,
        blob_path: Path,
        state: AccountingGraphState,
        profile: TaxpayerProfile
    ):
        """Сохранение результатов работы агентов в таблицы SQLite."""
        conn = get_connection(self.db_path)
        try:
            facts = state.facts
            doc_date_str = str(facts.doc_date.value) if facts.doc_date and facts.doc_date.value else date.today().isoformat()
            seller = str(facts.seller_name.value) if facts.seller_name and facts.seller_name.value else ""
            seller_nip = str(facts.seller_nip.value) if facts.seller_nip and facts.seller_nip.value else ""
            doc_nr = str(facts.doc_number.value) if facts.doc_number and facts.doc_number.value else ""
            netto = float(facts.netto.value) if facts.netto and facts.netto.value else 0.0
            vat = float(facts.vat.value) if facts.vat and facts.vat.value else 0.0
            brutto = float(facts.brutto.value) if facts.brutto and facts.brutto.value else 0.0
            
            # 0. Создаем служебную запись message и attachment, если нет
            cur_att = conn.execute("SELECT id FROM attachments WHERE sha256 = ?", (sha,)).fetchone()
            if cur_att:
                att_id = cur_att[0]
            else:
                max_msg = conn.execute("SELECT COALESCE(MAX(message_id), 0) + 1 FROM messages WHERE chat_id = 1").fetchone()[0]
                cur_msg = conn.execute(
                    """
                    INSERT INTO messages (chat_id, message_id, sent_at, source, raw)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (1, max_msg, datetime.now(timezone.utc).isoformat(), "live", "{}")
                )
                msg_id = cur_msg.lastrowid
                cur_att_ins = conn.execute(
                    """
                    INSERT INTO attachments (message_id, idx, sha256, orig_name)
                    VALUES (?, ?, ?, ?)
                    """,
                    (msg_id, 0, sha, orig_name)
                )
                att_id = cur_att_ins.lastrowid

            # 1. Запись в documents
            cur = conn.execute(
                """
                INSERT INTO documents (
                    attachment_id, doc_type, category, subcategory, confidence, decided_by,
                    doc_date, counterparty, nip, doc_number, net, vat, gross, currency, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    att_id,
                    facts.doc_type,
                    state.proposal.category if state.proposal else "do-wyjasnienia",
                    state.proposal.subcategory if state.proposal else None,
                    state.proposal.confidence if state.proposal else 0.9,
                    state.proposal.basis if state.proposal else "rule",
                    doc_date_str,
                    seller,
                    seller_nip,
                    doc_nr,
                    netto,
                    vat,
                    brutto,
                    facts.currency,
                    datetime.now(timezone.utc).isoformat()
                )
            )
            doc_id = cur.lastrowid

            # 2. Запись в kpir_entries
            if state.kpir_entry:
                kp = state.kpir_entry
                conn.execute(
                    """
                    INSERT INTO kpir_entries (
                        document_id, lp, entry_date, doc_number, counterparty_name, description,
                        col_7_przychody, col_8_pozostale_przych, col_9_razem_przychody,
                        col_10_zakup_towarow, col_11_koszty_uboczne, col_12_wynagrodzenia,
                        col_13_pozostale_wyd, col_14_razem_wydatki, vat_amount,
                        vehicle_usage, kup_ratio, vat_ratio, raw_facts_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        kp.lp,
                        kp.entry_date.isoformat(),
                        kp.doc_number,
                        kp.counterparty_name,
                        kp.description,
                        float(kp.col_7_przychody),
                        float(kp.col_8_pozostale_przychody),
                        float(kp.col_9_razem_przychody),
                        float(kp.col_10_zakup_towarow),
                        float(kp.col_11_koszty_uboczne),
                        float(kp.col_12_wynagrodzenia),
                        float(kp.col_13_pozostale_wydatki),
                        float(kp.col_14_razem_wydatki),
                        float(kp.vat_amount),
                        state.proposal.vehicle_usage_type if state.proposal else None,
                        float(state.proposal.kup_deductible_ratio) if state.proposal else 1.0,
                        float(state.proposal.vat_deductible_ratio) if state.proposal else 1.0,
                        facts.model_dump_json(),
                        datetime.now(timezone.utc).isoformat()
                    )
                )

            # 3. Запись в zus_declarations (если рассчитан ZUS)
            if state.zus_obligations:
                zus = state.zus_obligations
                conn.execute(
                    """
                    INSERT OR REPLACE INTO zus_declarations (
                        taxpayer_nip, period_month, stage, zbieg_tytulow, spoleczne_base, zdrowotna_base,
                        emerytalne, rentowe, chorobowe, wypadkowe, fundusz_pracy, skladka_zdrowotna,
                        total_spoleczne, total_do_zaplaty, forms_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile.nip,
                        zus.month,
                        zus.stage.value,
                        1 if zus.zbieg_tytulow else 0,
                        float(zus.spoleczne_base),
                        float(zus.zdrowotna_base),
                        float(zus.emerytalne),
                        float(zus.rentowe),
                        float(zus.chorobowe),
                        float(zus.wypadkowe),
                        float(zus.fundusz_pracy),
                        float(zus.skladka_zdrowotna),
                        float(zus.total_spoleczne),
                        float(zus.total_zus_do_zaplaty),
                        json.dumps(zus.forms_required),
                        datetime.now(timezone.utc).isoformat()
                    )
                )

            # 4. Запись в tax_advances (если рассчитан PIT)
            if state.tax_result:
                tx = state.tax_result
                conn.execute(
                    """
                    INSERT OR REPLACE INTO tax_advances (
                        taxpayer_nip, period_month, regime, income_ytd, costs_ytd, tax_base_ytd,
                        tax_due_ytd, advances_paid_prior, advance_to_pay, threshold_exceeded, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile.nip,
                        tx.month,
                        tx.regime.value,
                        float(tx.income_ytd),
                        float(tx.costs_ytd),
                        float(tx.tax_base_ytd),
                        float(tx.tax_due_ytd),
                        float(tx.advances_paid_prior),
                        float(tx.advance_to_pay),
                        1 if tx.threshold_exceeded else 0,
                        datetime.now(timezone.utc).isoformat()
                    )
                )

            conn.commit()
        finally:
            conn.close()

    def _route_to_archive(self, src_file: Path, state: AccountingGraphState):
        """
        Копирует документ в физическое дерево:
        archiwum/wg-daty-dokumentu/{YYYY}/{MM-miesiac}/{category}/{filename}
        """
        facts = state.facts
        doc_date = facts.doc_date.value if (facts.doc_date and isinstance(facts.doc_date.value, date)) else date.today()
        year = str(doc_date.year)
        month_folder = MonthNames.get_month_folder(doc_date.month)
        
        category_name = "koszty"
        if state.proposal:
            if state.proposal.kpir_column == 7:
                category_name = "przychody"
            elif "paliwo" in state.proposal.category.lower():
                category_name = "koszty_paliwo"
                
        target_dir = self.archive_dir / year / month_folder / category_name
        target_dir.mkdir(parents=True, exist_ok=True)
        
        seller_clean = (str(facts.seller_name.value) if facts.seller_name and facts.seller_name.value else "kontrahent").lower().replace(" ", "_")[:15]
        doc_nr_clean = (str(facts.doc_number.value) if facts.doc_number and facts.doc_number.value else "fv").replace("/", "_")
        target_filename = f"{doc_date.isoformat()}__{category_name}__{seller_clean}__{doc_nr_clean}{src_file.suffix}"
        
        target_path = target_dir / target_filename
        shutil.copy2(src_file, target_path)

    def generate_monthly_register(self, year: int, month: int) -> Path:
        """
        Генерирует официальный польский XLSX-реестр KPiR за указанный месяц с формулами и форматированием.
        """
        month_str = f"{year}-{month:02d}"
        output_file = self.registers_dir / f"{month_str}_rejestr.xlsx"
        
        conn = get_connection(self.db_path)
        try:
            entries = conn.execute(
                """
                SELECT lp, entry_date, doc_number, counterparty_name, description,
                       col_7_przychody, col_8_pozostale_przych, col_9_razem_przychody,
                       col_10_zakup_towarow, col_11_koszty_uboczne, col_12_wynagrodzenia,
                       col_13_pozostale_wyd, col_14_razem_wydatki, vat_amount, vehicle_usage
                FROM kpir_entries
                WHERE entry_date LIKE ?
                ORDER BY lp ASC
                """,
                (f"{month_str}%",)
            ).fetchall()
        finally:
            conn.close()

        workbook = xlsxwriter.Workbook(output_file)
        ws_kpir = workbook.add_worksheet("KPiR")
        ws_summary = workbook.add_worksheet("Podsumowanie")

        # Стили ячеек
        header_fmt = workbook.add_format({
            'bold': True, 'bg_color': '#1E293B', 'font_color': '#FFFFFF',
            'border': 1, 'align': 'center', 'valign': 'vcenter'
        })
        num_fmt = workbook.add_format({'num_format': '#,##0.00 zł', 'border': 1})
        bold_num_fmt = workbook.add_format({'num_format': '#,##0.00 zł', 'bold': True, 'border': 1, 'bg_color': '#F1F5F9'})
        text_fmt = workbook.add_format({'border': 1})
        center_fmt = workbook.add_format({'align': 'center', 'border': 1})

        # Заголовки колонок KPiR
        headers = [
            "Lp", "Data", "Nr Dowodu", "Kontrahent", "Opis operacji",
            "Kol. 7: Przychód", "Kol. 10: Towary", "Kol. 12: Płace",
            "Kol. 13: Pozostałe", "Kol. 14: Razem Koszty", "VAT Odliczony", "Uwagi"
        ]
        
        for col_idx, h in enumerate(headers):
            ws_kpir.write(0, col_idx, h, header_fmt)

        row_idx = 1
        for e in entries:
            ws_kpir.write(row_idx, 0, e[0], center_fmt)
            ws_kpir.write(row_idx, 1, e[1], center_fmt)
            ws_kpir.write(row_idx, 2, e[2], text_fmt)
            ws_kpir.write(row_idx, 3, e[3], text_fmt)
            ws_kpir.write(row_idx, 4, e[4], text_fmt)
            ws_kpir.write_number(row_idx, 5, e[5], num_fmt)  # Col 7
            ws_kpir.write_number(row_idx, 6, e[8], num_fmt)  # Col 10
            ws_kpir.write_number(row_idx, 7, e[10], num_fmt) # Col 12
            ws_kpir.write_number(row_idx, 8, e[11], num_fmt) # Col 13
            ws_kpir.write_number(row_idx, 9, e[12], bold_num_fmt) # Col 14
            ws_kpir.write_number(row_idx, 10, e[13], num_fmt) # VAT
            ws_kpir.write(row_idx, 11, e[14] or "", text_fmt)
            row_idx += 1

        # Строка итогов (Формулы Excel)
        if row_idx > 1:
            ws_kpir.write(row_idx, 4, "RAZEM ZA MIESIĄC:", header_fmt)
            ws_kpir.write_formula(row_idx, 5, f"=SUM(F2:F{row_idx})", bold_num_fmt)
            ws_kpir.write_formula(row_idx, 6, f"=SUM(G2:G{row_idx})", bold_num_fmt)
            ws_kpir.write_formula(row_idx, 7, f"=SUM(H2:H{row_idx})", bold_num_fmt)
            ws_kpir.write_formula(row_idx, 8, f"=SUM(I2:I{row_idx})", bold_num_fmt)
            ws_kpir.write_formula(row_idx, 9, f"=SUM(J2:J{row_idx})", bold_num_fmt)
            ws_kpir.write_formula(row_idx, 10, f"=SUM(K2:K{row_idx})", bold_num_fmt)

        # Автоматическая ширина колонок
        ws_kpir.set_column(0, 0, 6)
        ws_kpir.set_column(1, 1, 12)
        ws_kpir.set_column(2, 2, 18)
        ws_kpir.set_column(3, 3, 24)
        ws_kpir.set_column(4, 4, 30)
        ws_kpir.set_column(5, 10, 16)
        ws_kpir.set_column(11, 11, 20)

        # Лист Podsumowanie
        ws_summary.write(0, 0, f"PODSUMOWANIE KSIĘGOWE ZA {month_str}", header_fmt)
        ws_summary.write(2, 0, "Przychody ze sprzedaży (Kol. 7):", text_fmt)
        ws_summary.write_formula(2, 1, f"=KPiR!F{row_idx+1 if row_idx > 1 else 2}", bold_num_fmt)
        ws_summary.write(3, 0, "Koszty uzyskania przychodów (Kol. 14):", text_fmt)
        ws_summary.write_formula(3, 1, f"=KPiR!J{row_idx+1 if row_idx > 1 else 2}", bold_num_fmt)
        ws_summary.write(4, 0, "DOCHÓD / STRATA NETTO:", header_fmt)
        ws_summary.write_formula(4, 1, "=B3-B4", bold_num_fmt)

        ws_summary.set_column(0, 0, 38)
        ws_summary.set_column(1, 1, 20)

        workbook.close()
        return output_file
