import pandas as pd
from django.core.management.base import BaseCommand
from django.db import transaction
from app.models import NaturalPerson, User

class Command(BaseCommand):
    help = '检查并修正数据库人员状态与给定的Excel表是否一致'

    def add_arguments(self, parser):
        parser.add_argument('excel_path', type=str, help='Excel文件的路径（包含姓名、学号、状态）')
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='只显示差异和预期操作，不实际修改数据库'
        )

    def handle(self, *args, **options):
        excel_path = options['excel_path']
        is_dry_run = options['dry_run']

        if is_dry_run:
            self.stdout.write(self.style.NOTICE(">>> 当前模式：[DRY RUN] 只读检查，不会写入数据库 <<<"))

        # 1. 状态映射配置
        # 根据 show_info 定义：在校 -> UNDERGRADUATED, 毕业 -> GRADUATED
        STATUS_MAP = {
            "在校": NaturalPerson.GraduateStatus.UNDERGRADUATED,
            "毕业": NaturalPerson.GraduateStatus.GRADUATED,
            # TODO: 未来可扩展映射关系，如：
            # "休学": NaturalPerson.GraduateStatus.SUSPENDED,
        }

        # 2. 读取 Excel
        try:
            df = pd.read_excel(excel_path)
        except Exception as e:
            self.stderr.write(f"读取文件失败: {e}")
            return

        # 3. 初始化统计指标
        stats = {
            "total": len(df),
            "not_found": 0,
            "mismatch": 0,
            "updated": 0,
            "unknown_status": 0,
            "matched": 0
        }

        # 4. 使用原子事务包裹全过程，确保数据治理的安全性
        with transaction.atomic():
            for index, row in df.iterrows():
                name = str(row['姓名']).strip()
                stu_id = str(row['学号']).strip()
                excel_status_str = str(row['状态']).strip()

                # A. 查找人员 (YPPF 逻辑：NaturalPerson.person_id 是关联 User.username 的 OneToOne)
                person = NaturalPerson.objects.filter(
                    name=name, 
                    person_id__username=stu_id
                ).first()

                if not person:
                    self.stdout.write(self.style.WARNING(f" [缺失] 第{index+2}行找不到人员: {name} ({stu_id})"))
                    stats["not_found"] += 1
                    continue

                # B. 解析数据库当前状态
                # 利用 models.py 里 show_info 的逻辑或 get_xxx_display
                db_status_str = "在校" if person.status == NaturalPerson.GraduateStatus.UNDERGRADUATED else "已毕业"

                # C. 比对状态
                if db_status_str == excel_status_str:
                    stats["matched"] += 1
                    continue
                
                # D. 发现不一致
                stats["mismatch"] += 1
                target_code = STATUS_MAP.get(excel_status_str)

                if target_code is None:
                    self.stdout.write(self.style.NOTICE(f" [未知] 第 {index+2} 行无法映射Excel状态: '{excel_status_str}' (姓名: {name})"))
                    stats["unknown_status"] += 1
                    continue

                # E. 执行或模拟修正
                self.stdout.write(self.style.ERROR(
                    f" [差异] 第 {index+2} 行 {name} ({stu_id}): 数据库({db_status_str}) -> Excel({excel_status_str})"
                ))

                if not is_dry_run:
                    person.status = target_code
                    person.save()
                    stats["updated"] += 1

        # 5. 打印运维报告
        self.stdout.write("\n" + "="*30)
        self.stdout.write("      数据核对运维报告")
        self.stdout.write("="*30)
        self.stdout.write(f" 总处理行数: {stats['total']}")
        self.stdout.write(f" 状态完全一致: {stats['matched']}")
        self.stdout.write(f" [待修正] 状态不一致: {stats['mismatch']}")
        self.stdout.write(f" [异常] 数据库查无此人: {stats['not_found']}")
        self.stdout.write(f" [异常] Excel状态无法识别: {stats['unknown_status']}")
        
        if is_dry_run:
            self.stdout.write(self.style.SUCCESS(f"\n预览完毕，预计修正: {stats['updated'] + (stats['mismatch'] - stats['unknown_status'])} 条数据"))
        else:
            self.stdout.write(self.style.SUCCESS(f"\n数据库修正成功，实际更新: {stats['updated']} 条数据"))