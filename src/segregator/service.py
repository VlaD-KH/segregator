"""
src/segregator/service.py
Связующий сервисный слой (Service Layer API) платформы Segregator.
Объединяет SQLite базу данных, хранилище blobs, мультиагентный граф,
раскладку файлов в дерево archiwum/ и экспорт XLSX-реестров.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import xlsxwriter

from segregator.domain.models import (
    DataSource,
    DocumentFacts,
    ExtractedField,
    BookingProposal,
    TaxpayerProfile,
    TaxRegime,
    SyncState,
    mask_sensitive_fields,
)
from segregator.orchestrator.state import AccountingGraphState
from segregator.orchestrator.graph import build_accounting_graph
from segregator.db.migrate import get_connection, migrate
from segregator.ingest.blobs import store_blob as ingest_store_blob, blob_relative_path
from segregator.accounting.period import PeriodTotals, close_month, next_lp
from segregator.domain.models import ZUSObligations
from segregator.domain.zus import ZUSCalculator
from segregator.tax.pit import MonthlyTaxResult, PITCalculator


@dataclass(frozen=True)
class PeriodClosing:
    """Итог закрытия месяца: агрегат книги и посчитанные от него обязательства."""
    totals: PeriodTotals
    zus: ZUSObligations
    tax: MonthlyTaxResult


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
        # Все пути выводятся из одного workspace_root. Раньше здесь был отдельный
        # SEGREGATOR_ARCHIVE_DIR, который переносил только archive_dir, а БД, blobs/
        # и rejestry/ оставлял на месте: тест «изолирован в tmp_path» при этом писал
        # в боевой архив. Переключатель поднят на уровень CLI, где выбирается корень.
        self.root = workspace_root
        self.db_path = db_path or (workspace_root / "segregator.db")
        self.blobs_dir = workspace_root / "blobs"
        self.archive_dir = workspace_root / "archiwum" / "wg-daty-dokumentu"
        self.registers_dir = workspace_root / "rejestry"
        self.graph = build_accounting_graph()
        
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.registers_dir.mkdir(parents=True, exist_ok=True)
        
        migrate(self.db_path)

    def store_blob(self, content_bytes: bytes, original_filename: str = "") -> Tuple[str, Path]:
        """
        Сохраняет контент через CAS в blobs/ab/cd/hash.ext.
        """
        ext = Path(original_filename).suffix.lower() if original_filename else ".bin"
        if not ext:
            ext = ".bin"
            
        sha = hashlib.sha256(content_bytes).hexdigest()
        rel_path = blob_relative_path(sha, ext)
        target_file = self.root / rel_path
        
        if not target_file.exists():
            target_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = target_file.with_name(target_file.name + ".part")
            try:
                tmp.write_bytes(content_bytes)
                tmp.replace(target_file)
            finally:
                tmp.unlink(missing_ok=True)
            
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO blobs (sha256, bytes, mime, stored_path, ocr_state)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sha, len(content_bytes), "application/octet-stream", rel_path.as_posix(), "ocr")
            )
            conn.commit()
        finally:
            conn.close()

        return sha, target_file

    def get_sync_state(self, nip: str) -> SyncState:
        """Считывает водяные знаки синхронизации для налогоплательщика."""
        conn = get_connection(self.db_path)
        try:
            # NB: запрос ниже не фильтрует по nip — ни kpir_entries, ни documents
            # не несут идентификатора налогоплательщика (система однопользовательская,
            # см. CLAUDE.md). Параметр используется только для sync_watermarks ниже.
            # Join через kpir_entries, а не kpir_quarantine, — намеренно: карантинный
            # документ не должен считаться синхронизированным, иначе он ушёл бы в
            # skipped_idle и никогда не переобработался бы после починки причины эскалации.
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
        2. Прогон через мультиагентный граф
        3. Запись в SQLite
        4. Раскладка физического файла в archiwum/
        """
        blob_ref = ingest_store_blob(self.root, file_path)
        sha = blob_ref.sha256
        blob_file = blob_ref.path
        
        # Регистрация в таблице blobs
        conn = get_connection(self.db_path)
        try:
            rel_path = blob_relative_path(sha, file_path.suffix.lower() or ".bin").as_posix()
            conn.execute(
                """
                INSERT OR IGNORE INTO blobs (sha256, bytes, mime, stored_path, ocr_state)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sha, file_path.stat().st_size, "application/octet-stream", rel_path, "ocr")
            )
            conn.commit()
        finally:
            conn.close()
        
        sync_state = self.get_sync_state(profile.nip)
        
        target_doc_date = custom_facts.doc_date if (custom_facts and custom_facts.doc_date) else date.today()

        # Выручка года по уже проведённым документам. Узлы графа не видят БД,
        # поэтому цифру вкладывает сервис: без неё agent03 подставлял заглушку,
        # а на ryczałcie вовсе отказывался считать.
        conn = get_connection(self.db_path)
        try:
            ytd_przychody, _ = self._ytd_from_ledger(conn, target_doc_date.year, target_doc_date.month)
        finally:
            conn.close()

        initial_state = AccountingGraphState(
            raw_input=sha,
            target_date=target_doc_date,
            taxpayer_profile=profile,
            sync_state=sync_state,
            facts=custom_facts,
            ytd_przychody=ytd_przychody,
        )

        final_state = self.graph.invoke(initial_state)

        if not final_state.is_delta_empty and final_state.facts:
            self._save_results_to_db(sha, file_path.name, blob_file, final_state, profile)
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
        """Сохранение результатов работы агентов в таблицы SQLite с маскированием IBAN."""
        conn = get_connection(self.db_path)
        try:
            facts = state.facts
            doc_date_str = facts.doc_date.isoformat() if facts.doc_date else date.today().isoformat()
            seller = facts.seller_name
            seller_nip = facts.seller_nip
            doc_nr = facts.doc_number
            netto = float(facts.netto)
            vat = float(facts.vat)
            brutto = float(facts.brutto)
            
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

            # ON CONFLICT вместо голого INSERT: attachment_id UNIQUE (0001:35), а
            # process_document повторно вызывается для документов, не попавших в
            # kpir_entries (карантин, см. get_sync_state) — без этого повтор падал
            # бы IntegrityError вместо переобработки.
            cur = conn.execute(
                """
                INSERT INTO documents (
                    attachment_id, doc_type, category, subcategory, confidence, decided_by,
                    doc_date, counterparty, nip, doc_number, net, vat, gross, currency, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(attachment_id) DO NOTHING
                """,
                (
                    att_id,
                    facts.doc_type.value,
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
            if cur.rowcount:
                doc_id = cur.lastrowid
            else:
                doc_id = conn.execute(
                    "SELECT id FROM documents WHERE attachment_id = ?", (att_id,)
                ).fetchone()[0]

            # Маскируем ДО ветвления: набор полей не должен зависеть от того,
            # завелась ли запись KPiR. Проверка идёт по имени поля, поэтому
            # numer_konta/nr_konta/rachunek ловятся наравне с iban.
            facts_dict = mask_sensitive_fields(facts.model_dump())

            # Human Gate: проводка в KPiR — только для подтверждённого документа.
            # agent02 заполняет kpir_entry всегда, эскалация это или нет (graph.py:
            # порядок узлов agent02 -> agent03 -> условный переход), поэтому раньше
            # запись шла по одному условию «kpir_entry есть», а status не смотрелась
            # вовсе. Любой status кроме "completed" (по факту — только
            # "escalated_to_human", см. state.py) уходит в kpir_quarantine — тем же
            # набором колонок, без lp (номер KPiR не выдаётся непроведённому
            # документу — иначе в реестре осталась бы дыра) и с escalation_reason.
            completed = state.status == "completed"

            if state.kpir_entry:
                kp = state.kpir_entry
                vehicle_usage = "mixed" if (state.proposal and state.proposal.pit_cost_ratio == 0.75) else None
                kup_ratio = float(state.proposal.pit_cost_ratio) if (state.proposal and state.proposal.pit_cost_ratio is not None) else 1.0
                vat_ratio = float(state.proposal.vat_deduction_ratio) if (state.proposal and state.proposal.vat_deduction_ratio is not None) else 1.0

                if completed:
                    # MAX(lp)+1 в пределах года, в той же транзакции — образец уже
                    # есть в этом файле (max_msg выше). kp.lp — константа 1 из
                    # nodes.py (там нет доступа к БД), реальный номер даётся здесь.
                    lp = next_lp(conn, kp.entry_date)
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
                            lp,
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
                            vehicle_usage,
                            kup_ratio,
                            vat_ratio,
                            json.dumps(facts_dict),
                            datetime.now(timezone.utc).isoformat()
                        )
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO kpir_quarantine (
                            document_id, lp, entry_date, doc_number, counterparty_name, description,
                            col_7_przychody, col_8_pozostale_przych, col_9_razem_przychody,
                            col_10_zakup_towarow, col_11_koszty_uboczne, col_12_wynagrodzenia,
                            col_13_pozostale_wyd, col_14_razem_wydatki, vat_amount,
                            vehicle_usage, kup_ratio, vat_ratio, raw_facts_json,
                            escalation_reason, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            doc_id,
                            None,
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
                            vehicle_usage,
                            kup_ratio,
                            vat_ratio,
                            json.dumps(facts_dict),
                            state.escalation_reason or "Причина эскалации не указана",
                            datetime.now(timezone.utc).isoformat()
                        )
                    )

            # ZUS/PIT — тоже только для подтверждённого документа. agent03 считает
            # их ещё до шлюза (graph.py: agent03 -> условный переход), но цифры,
            # основанные на непроверенном документе, не должны попадать в ленту,
            # которую 0003 сделал append-only ради доверия к истории.
            if completed and state.zus_obligations:
                self._write_zus(conn, profile.nip, state.zus_obligations)

            if completed and state.tax_result:
                self._write_tax_advance(conn, profile.nip, state.tax_result)

            # Аудит — независимо от status. AuditEntry дописывался в семи узлах
            # nodes.py и не сохранялся никуда; это единственное место, где у записи
            # есть document_id, поэтому персист идёт здесь, одной транзакцией с
            # остальным.
            for entry in state.audit_trail:
                conn.execute(
                    """
                    INSERT INTO audit_trail (document_id, node_name, action, details, confidence, ts)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        entry.node_name,
                        entry.action,
                        entry.details,
                        entry.confidence,
                        entry.timestamp.isoformat(),
                    )
                )

            conn.commit()
        finally:
            conn.close()

    # --- append-only запись помесячных обязательств ------------------------------
    #
    # Раньше здесь стоял INSERT OR REPLACE под UNIQUE (taxpayer_nip, period_month).
    # Миграция 0003 сняла это ограничение и заменила частичным индексом по
    # superseded_at IS NULL — под ним OR REPLACE не заменяет строку, а УДАЛЯЕТ
    # прошлую вместе с её created_at. Инвариант 5 DATA_BOUNDARY.md требует
    # append-only, поэтому прежняя строка закрывается, а не исчезает.

    def _write_zus(self, conn: sqlite3.Connection, nip: str, zus: ZUSObligations) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            UPDATE zus_declarations SET superseded_at = ?
            WHERE taxpayer_nip = ? AND period_month = ? AND superseded_at IS NULL
            """,
            (now, nip, zus.month),
        )
        conn.execute(
            """
            INSERT INTO zus_declarations (
                taxpayer_nip, period_month, stage, zbieg_tytulow, spoleczne_base, zdrowotna_base,
                emerytalne, rentowe, chorobowe, wypadkowe, fundusz_pracy, skladka_zdrowotna,
                total_spoleczne, total_do_zaplaty, forms_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nip,
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
                now,
            ),
        )

    def _write_tax_advance(self, conn: sqlite3.Connection, nip: str, tx: MonthlyTaxResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            UPDATE tax_advances SET superseded_at = ?
            WHERE taxpayer_nip = ? AND period_month = ? AND superseded_at IS NULL
            """,
            (now, nip, tx.month),
        )
        conn.execute(
            """
            INSERT INTO tax_advances (
                taxpayer_nip, period_month, regime, income_ytd, costs_ytd, tax_base_ytd,
                tax_due_ytd, advances_paid_prior, advance_to_pay, threshold_exceeded, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nip,
                tx.month,
                tx.regime.value,
                float(tx.income_ytd),
                float(tx.costs_ytd),
                float(tx.tax_base_ytd),
                float(tx.tax_due_ytd),
                float(tx.advances_paid_prior),
                float(tx.advance_to_pay),
                1 if tx.threshold_exceeded else 0,
                now,
            ),
        )

    # --- закрытие месяца ---------------------------------------------------------

    @staticmethod
    def _ytd_from_ledger(conn: sqlite3.Connection, year: int, up_to_month: int) -> tuple[Decimal, Decimal]:
        """Выручка и расходы с начала года по конец `up_to_month`, из проводок.

        Месяц без проводок даёт ноль, а не отказ: JDG, открытая в середине года,
        законно не имеет книги за январь. Отказ считать относится только к
        закрываемому периоду и проверяется в close_period отдельно.
        """
        przychody = Decimal("0.00")
        koszty = Decimal("0.00")
        for month in range(1, up_to_month + 1):
            try:
                totals = close_month(conn, f"{year}-{month:02d}")
            except ValueError:
                continue
            przychody += totals.przychody
            koszty += totals.koszty
        return przychody, koszty

    def close_period(self, profile: TaxpayerProfile, period: str) -> PeriodClosing:
        """Официальный расчёт месяца: агрегаты из книги, ZUS и PIT от них.

        Отдельный шаг после проводки всех документов периода. Оценка, которую
        agent03 делает по одному документу, знает только текущую бумагу —
        здесь считается по всей книге, и результат закрывает эту оценку.

        Месяц без проводок — ValueError, а не нули: отсутствие данных и нулевой
        доход разные вещи, вторая молча отдала бы декларацию за необработанный
        месяц.
        """
        year, month = int(period[:4]), int(period[5:7])
        conn = get_connection(self.db_path)
        try:
            # Отказ считать пустой период приходит отсюда — close_month падает сам.
            totals = close_month(conn, period)

            ytd_przychody, ytd_koszty = self._ytd_from_ledger(conn, year, month)

            regime = profile.jdg_tax_regime or TaxRegime.SKALA
            zus = ZUSCalculator.calculate_monthly_obligations(
                profile=profile,
                target_month=date(year, month, 1),
                jdg_monthly_profit=totals.dochod,
                annual_revenue=ytd_przychody,
            )

            # Кумулятивные взносы — из истории закрытых месяцев плюс текущий.
            # Берутся только действующие строки: пересчитанный месяц не должен
            # считаться дважды.
            prior_spoleczne, prior_zdrowotna = conn.execute(
                """
                SELECT COALESCE(SUM(total_spoleczne), 0), COALESCE(SUM(skladka_zdrowotna), 0)
                FROM zus_declarations
                WHERE taxpayer_nip = ? AND period_month < ? AND superseded_at IS NULL
                """,
                (profile.nip, period),
            ).fetchone()

            # Ранее уплаченные авансы — кумулятивный налог прошлого месяца.
            # Аванс за месяц = налог нарастающим итогом минус уже уплаченное;
            # раньше сюда не передавалось ничего, и аванс платился заново каждый месяц.
            prior_row = conn.execute(
                """
                SELECT tax_due_ytd FROM tax_advances
                WHERE taxpayer_nip = ? AND period_month < ? AND superseded_at IS NULL
                ORDER BY period_month DESC LIMIT 1
                """,
                (profile.nip, period),
            ).fetchone()
            advances_paid_prior = Decimal(str(prior_row[0])) if prior_row else Decimal("0.00")

            tax = PITCalculator.calculate_monthly_jdg_advance(
                month=period,
                regime=regime,
                income_ytd=ytd_przychody,
                costs_ytd=ytd_koszty,
                social_zus_paid_ytd=Decimal(str(prior_spoleczne)) + zus.total_spoleczne,
                health_zus_paid_ytd=Decimal(str(prior_zdrowotna)) + zus.skladka_zdrowotna,
                advances_paid_prior=advances_paid_prior,
                ryczalt_rate=profile.jdg_ryczalt_rate if regime == TaxRegime.RYCZALT else None,
            )

            self._write_zus(conn, profile.nip, zus)
            self._write_tax_advance(conn, profile.nip, tax)
            conn.commit()
        finally:
            conn.close()

        return PeriodClosing(totals=totals, zus=zus, tax=tax)

    def _route_to_archive(self, src_file: Path, state: AccountingGraphState):
        """
        Копирует документ в физическое дерево:
        archiwum/wg-daty-dokumentu/{YYYY}/{MM-miesiac}/{category}/{filename}
        """
        facts = state.facts
        doc_date = facts.doc_date or date.today()
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
        
        seller_clean = (facts.seller_name or "kontrahent").lower().replace(" ", "_")[:15]
        doc_nr_clean = (facts.doc_number or "fv").replace("/", "_")
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

        header_fmt = workbook.add_format({
            'bold': True, 'bg_color': '#1E293B', 'font_color': '#FFFFFF',
            'border': 1, 'align': 'center', 'valign': 'vcenter'
        })
        num_fmt = workbook.add_format({'num_format': '#,##0.00 zł', 'border': 1})
        bold_num_fmt = workbook.add_format({'num_format': '#,##0.00 zł', 'bold': True, 'border': 1, 'bg_color': '#F1F5F9'})
        text_fmt = workbook.add_format({'border': 1})
        center_fmt = workbook.add_format({'align': 'center', 'border': 1})

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
            ws_kpir.write_number(row_idx, 5, e[5], num_fmt)
            ws_kpir.write_number(row_idx, 6, e[8], num_fmt)
            ws_kpir.write_number(row_idx, 7, e[10], num_fmt)
            ws_kpir.write_number(row_idx, 8, e[11], num_fmt)
            ws_kpir.write_number(row_idx, 9, e[12], bold_num_fmt)
            ws_kpir.write_number(row_idx, 10, e[13], num_fmt)
            ws_kpir.write(row_idx, 11, e[14] or "", text_fmt)
            row_idx += 1

        if row_idx > 1:
            ws_kpir.write(row_idx, 4, "RAZEM ZA MIESIĄC:", header_fmt)
            ws_kpir.write_formula(row_idx, 5, f"=SUM(F2:F{row_idx})", bold_num_fmt)
            ws_kpir.write_formula(row_idx, 6, f"=SUM(G2:G{row_idx})", bold_num_fmt)
            ws_kpir.write_formula(row_idx, 7, f"=SUM(H2:H{row_idx})", bold_num_fmt)
            ws_kpir.write_formula(row_idx, 8, f"=SUM(I2:I{row_idx})", bold_num_fmt)
            ws_kpir.write_formula(row_idx, 9, f"=SUM(J2:J{row_idx})", bold_num_fmt)
            ws_kpir.write_formula(row_idx, 10, f"=SUM(K2:K{row_idx})", bold_num_fmt)

        ws_kpir.set_column(0, 0, 6)
        ws_kpir.set_column(1, 1, 12)
        ws_kpir.set_column(2, 2, 18)
        ws_kpir.set_column(3, 3, 24)
        ws_kpir.set_column(4, 4, 30)
        ws_kpir.set_column(5, 10, 16)
        ws_kpir.set_column(11, 11, 20)

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
