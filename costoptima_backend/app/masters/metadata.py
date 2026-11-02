from sqlalchemy import (MetaData, Table, Column, Integer, BigInteger, String, Text, DateTime, ForeignKey, UniqueConstraint, Numeric)
from datetime import datetime
import re
metadata = MetaData()

def table(name, *columns, **kw):
    return Table(name, metadata, *columns, mysql_engine="InnoDB", mysql_charset="utf8mb4", **kw)

def pk(name): return Column(name,Integer,primary_key=True,autoincrement=True)
masters=table("master_definition",pk("master_id"),Column("master_code",String(100),unique=True,nullable=False),Column("master_name",String(255),nullable=False),Column("sheet_name",String(100),nullable=False),Column("header_kind",String(10),nullable=False),Column("unique_column",String(255)),Column("norms_position",Integer),Column("sort_order",Integer,nullable=False),Column("view_name",String(64),unique=True,nullable=False))
merge_columns=table("master_merge_column",pk("merge_column_id"),Column("master_id",ForeignKey("master_definition.master_id"),nullable=False),Column("column_name",String(255),nullable=False),UniqueConstraint("master_id","column_name"))
families=table("master_family",pk("family_id"),Column("folder_path",String(255),unique=True,nullable=False),Column("display_name",String(255),nullable=False),Column("sort_order",Integer,nullable=False))
sources=table("master_source",pk("source_id"),Column("master_id",ForeignKey("master_definition.master_id"),nullable=False),Column("family_id",ForeignKey("master_family.family_id"),nullable=False),Column("data_table_name",String(64),unique=True,nullable=False),Column("source_file",String(500),nullable=False),Column("row_count",Integer,nullable=False,default=0),Column("expected_rows",Integer,nullable=False),Column("merge_order",Integer,nullable=False),Column("updated_at",DateTime,default=datetime.utcnow),Column("updated_by",String(255)),UniqueConstraint("master_id","family_id"))
columns=table("master_column",pk("column_id"),Column("source_id",ForeignKey("master_source.source_id"),nullable=False),Column("position",Integer,nullable=False),Column("column_key",String(64),nullable=False),Column("header_text",Text),Column("group_text",Text),Column("sql_type",String(40),nullable=False),Column("storage_type",String(40),nullable=False),UniqueConstraint("source_id","column_key"),UniqueConstraint("source_id","position"))
batches=table("import_batch",pk("batch_id"),Column("source_id",ForeignKey("master_source.source_id"),nullable=False,index=True),Column("import_mode",String(16),nullable=False),Column("file_name",String(255),nullable=False),Column("rows_inserted",Integer,default=0,nullable=False),Column("rows_updated",Integer,default=0,nullable=False),Column("rows_rejected",Integer,default=0,nullable=False),Column("rows_skipped",Integer,default=0,nullable=False),Column("created_by",String(255),nullable=False),Column("actor_user_id",Integer,nullable=False),Column("session_ref",String(64)),Column("request_id",String(36)),Column("created_at",DateTime,default=datetime.utcnow,nullable=False),Column("finished_at",DateTime),Column("status",String(24),nullable=False),Column("file_sha256",String(64)),Column("staging_file",String(500)))
errors=table("import_error",pk("error_id"),Column("batch_id",ForeignKey("import_batch.batch_id"),nullable=False,index=True),Column("row_number",Integer),Column("column_key",String(64)),Column("message",String(1000),nullable=False))
output_columns=table("master_output_column",pk("output_column_id"),Column("master_id",ForeignKey("master_definition.master_id"),nullable=False),Column("position",Integer,nullable=False),Column("column_key",String(64)),Column("header_text",Text),Column("group_text",Text),UniqueConstraint("master_id","position"))
# Per-field changes/history are provided by audit_events, never a duplicate change_log.

def sql_type(name):
    return Numeric(20,6) if name=='DECIMAL(20,6)' else BigInteger if name.startswith('BIGINT') else Text if name=='TEXT' else String(int(re.fullmatch(r'VARCHAR\((\d+)\)',name).group(1)))
