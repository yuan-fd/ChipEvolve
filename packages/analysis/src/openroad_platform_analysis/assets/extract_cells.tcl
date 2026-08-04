# ─────────────────────────────────────────────────────────────────────
# extract_cells.tcl — 从 ORFS 的 ODB 里导出每个标准单元的物理坐标
#
# 在容器内跑（ODB 是 OpenROAD 的数据库，只有 openroad 能读）:
#   openroad -exit \
#     -metrics /dev/null \
#     ODB_FILE=... OUT_CSV=... flow/analysis_tcl/extract_cells.tcl
#
# 实际由 analysis/parsers/cell_coords.py 通过 docker 调用，环境变量传参：
#   ODB_FILE  输入 .odb（如 results/nangate45/<design>/base/3_place.odb）
#   OUT_CSV   输出 CSV
#   OUT_META  输出 die/core 边界（JSON）
#
# CSV 列: inst_name,cell_type,x1,y1,x2,y2,width,height,is_sequential   单位 µm
# ─────────────────────────────────────────────────────────────────────

proc env_or {name default} {
  if {[info exists ::env($name)]} { return $::env($name) }
  return $default
}

set odb_file [env_or ODB_FILE ""]
set out_csv  [env_or OUT_CSV  "cells.csv"]
set out_meta [env_or OUT_META "layout_meta.json"]

if {$odb_file eq "" || ![file exists $odb_file]} {
  puts "ERROR: ODB_FILE 不存在: $odb_file"
  exit 1
}

read_db $odb_file

set db    [ord::get_db]
set chip  [$db getChip]
set block [$chip getBlock]

# ODB 内部单位是 DBU，除以 dbu-per-micron 得到 µm
set dbu [$block getDefUnits]
proc um {v} { global dbu; return [expr {double($v) / double($dbu)}] }

# ── die / core 边界 ──
set die [$block getDieArea]
set die_x1 [um [$die xMin]] ; set die_y1 [um [$die yMin]]
set die_x2 [um [$die xMax]] ; set die_y2 [um [$die yMax]]

# core 用 rows 的包络推出来（不同版本 getCoreArea 未必存在）
set core_x1 $die_x1 ; set core_y1 $die_y1
set core_x2 $die_x2 ; set core_y2 $die_y2
if {![catch {set core [$block getCoreArea]}]} {
  set core_x1 [um [$core xMin]] ; set core_y1 [um [$core yMin]]
  set core_x2 [um [$core xMax]] ; set core_y2 [um [$core yMax]]
}

# ── 逐个 instance 导出 bbox ──
set fh [open $out_csv w]
puts $fh "inst_name,cell_type,x1,y1,x2,y2,width,height,is_sequential"

set n 0
set seq 0
foreach inst [$block getInsts] {
  set name   [$inst getName]
  set master [$inst getMaster]
  set mname  [$master getName]

  set bbox [$inst getBBox]
  set x1 [um [$bbox xMin]] ; set y1 [um [$bbox yMin]]
  set x2 [um [$bbox xMax]] ; set y2 [um [$bbox yMax]]
  set w  [expr {$x2 - $x1}] ; set h [expr {$y2 - $y1}]

  # 时序单元判定：优先问 master，问不到就看名字
  set is_seq 0
  if {[catch {set is_seq [expr {[$master isSequential] ? 1 : 0}]}]} {
    if {[regexp -nocase {DFF|LATCH|SDFF|_FF} $mname]} { set is_seq 1 }
  }
  if {$is_seq} { incr seq }

  # 名字里可能有逗号，用引号包起来
  puts $fh "\"$name\",$mname,$x1,$y1,$x2,$y2,$w,$h,$is_seq"
  incr n
}
close $fh

# ── 元信息 ──
set mf [open $out_meta w]
puts $mf "{"
puts $mf "  \"die_bounds\":  {\"x1\": $die_x1, \"y1\": $die_y1, \"x2\": $die_x2, \"y2\": $die_y2},"
puts $mf "  \"core_bounds\": {\"x1\": $core_x1, \"y1\": $core_y1, \"x2\": $core_x2, \"y2\": $core_y2},"
puts $mf "  \"total_cells\": $n,"
puts $mf "  \"sequential_cells\": $seq,"
puts $mf "  \"dbu_per_micron\": $dbu"
puts $mf "}"
close $mf

puts "extract_cells: 导出 $n 个单元（其中时序单元 $seq）→ $out_csv"
exit 0
