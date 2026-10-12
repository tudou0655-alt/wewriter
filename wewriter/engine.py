import json
import threading
from datetime import datetime, timedelta, timezone
from .api import ApiError, Cancelled, EveryInfra, Model, Transport, digest, parse_date, wechat_url
from .config import validate, migrate
from .db import stamp
from .hotspots import count, rank


class Engine:
    def __init__(self, db, vault, notify=lambda text: None, source_factory=None, model_factory=Model,
                 research_factory=None, profile_dir=None):
        self.db, self.vault, self.notify = db, vault, notify
        self.source_factory, self.model_factory = source_factory, model_factory
        from .research import WebResearch
        self.research_factory = research_factory or WebResearch
        self.profile_dir = profile_dir
        self.cancel = threading.Event()
        self.lock = threading.Lock()

    def stop(self):
        self.cancel.set()

    def check(self):
        if self.cancel.is_set():
            raise Cancelled("任务已停止，可在运行记录中重试")

    def run(self, run_key, config, reset_cancel=True, shared_source=None):
        if not self.lock.acquire(blocking=False):
            raise ValueError("已有任务运行中")
        if reset_cancel:
            self.cancel.clear()
        job = None
        source = shared_source
        owns_source = shared_source is None
        try:
            config = migrate(config)
            validate(config)
            job = self.db.job(run_key, config)
            if job["status"] in ("已完成", "无新文章", "已清理") or (job['status'] == '部分完成' and json.loads(job['config']).get('writing_mode') != 'separate_web'):
                return job["status"]
            # Retry uses the original non-secret configuration and time window.
            config = migrate(json.loads(job["config"]))
            validate(config)
            existing = self.db.rows("SELECT id FROM drafts WHERE job_id=?", (job["id"],))
            if existing:
                self.db.update_job(job["id"], status="已完成", message="已有稿件，未重复生成")
                return "已完成"
            # A retry is a new execution attempt. Old warnings remain visible in
            # the previous result message/history, but must not be mixed into the
            # current attempt or shown as if they happened again.
            self.db.update_job(job["id"], status="运行中", message="正在准备任务",
                               warnings="[]")
            transport = Transport(self.cancel)
            if source is None:
                if self.source_factory:
                    source = self.source_factory(self.vault.get("everyinfra"), transport)
                else:
                    from .weread import WeReadSource
                    source = WeReadSource(
                        self.vault.get("weread"), self.db, self.cancel,
                        state_saver=lambda value: self.vault.set_many({'weread': value}),
                        profile_dir=self.profile_dir)
            def model(name):
                return self.model_factory(name, config["providers"][name], self.vault.get(name), transport)
            summarizer = model(config["summary_provider"])
            writer = model(config["writer_provider"])
            selected = json.loads(job["selected"])
            warnings = []

            def warn(message):
                warnings.append(message)
                self.db.update_job(job["id"], warnings=json.dumps(warnings, ensure_ascii=False))
                self.notify(message)

            if not selected:
                accounts = self.db.accounts(enabled=True)
                if not accounts:
                    raise ValueError("请先添加并启用至少一个公众号")
                now = datetime.fromisoformat(job["created_at"])
                earliest = now - timedelta(hours=config["lookback_hours"])
                candidates, successes = {}, 0
                recollect = set()
                for account in accounts:
                    self.check()
                    self.notify(f"正在同步：{account['name']}")
                    try:
                        items = source.articles(account["identifier"], config["per_account_limit"])
                        successes += 1
                    except ApiError as exc:
                        if exc.fatal:
                            raise
                        warn(f"{account['name']}：{exc}")
                        continue
                    if len(items) >= config["per_account_limit"]:
                        warn(f"{account['name']} 返回条数达到单账号上限，时间窗内可能仍有未覆盖文章")
                    for item in items:
                        self.check()
                        try:
                            if not isinstance(item, dict):
                                raise ValueError("文章条目格式无效")
                            if item.get("is_deleted") in (True, 1, "true"):
                                continue
                            published = parse_date(item.get("posted_at"))
                            if not earliest <= published <= now:
                                continue
                            url = wechat_url(item.get("url", ""))
                            title = str(item.get("title") or "").strip()
                            if not title:
                                raise ValueError("标题缺失")
                            article_id = self.db.save_article({"source_key": digest(url), "account_name": account["name"],
                                "title": title, "url": url, "published_at": published.isoformat()})
                            if self.db.rows('SELECT cleared FROM articles WHERE id=?', (article_id,))[0]['cleared']:
                                if not config.get('allow_recollect_cleared', False):
                                    continue
                                recollect.add(article_id)
                            reads, likes = count(item.get('read_count')), count(item.get('like_count'))
                            observed = stamp()
                            self.db.execute('INSERT OR REPLACE INTO article_metrics VALUES(?,?,?,?)',
                                            (article_id, reads, likes, observed))
                            self.db.execute('INSERT OR REPLACE INTO job_metrics VALUES(?,?,?,?,?)',
                                            (job['id'], article_id, reads, likes, observed))
                            candidates[article_id] = {'published': published, 'reads': reads, 'likes': likes}
                        except (ValueError, OverflowError, OSError):
                            warn(f"{account['name']}：一条文章的标题、URL 或发布时间无效，已跳过")
                if not successes:
                    raise ApiError("所有公众号采集失败；" + (warnings[0] if warnings else "请检查登录或接口配置"))
                # Articles already used in a saved draft are not consumed again by daily/manual synchronization.
                used = set()
                for draft in self.db.rows("SELECT sources FROM drafts"):
                    used.update(s["article_id"] for s in json.loads(draft["sources"]) if 'article_id' in s)
                used.difference_update(recollect)
                mode = 'hot' if config.get('writing_mode') == 'separate_web' else config.get('selection_mode', 'recent')
                eligible = rank(candidates, mode, config.get('min_reads', 0))
                limit = config.get('draft_count', 3) if config.get('writing_mode') == 'separate_web' else config['article_limit']
                available = [i for i in eligible if i not in used]
                # Keep the complete ranked queue for independent writing. If a
                # top article cannot provide usable source material, the next
                # hottest article can fill that output slot without recollecting.
                selected = available if config.get('writing_mode') == 'separate_web' else available[:limit]
                if config.get('selection_mode') == 'hot' and candidates and not eligible:
                    raise ValueError('没有达到阅读量门槛的文章，或接口未提供阅读量；请查看文章库，调整门槛或时间范围')
                self.db.update_job(job["id"], selected=json.dumps(selected))
                if not selected:
                    status = "失败" if warnings else "无新文章"
                    message = "采集存在异常且没有可用新文章，请查看警告后重试" if warnings else "时间范围内没有未使用的新文章"
                    self.db.update_job(job["id"], status=status, message=message)
                    return status
            if config.get('writing_mode') == 'separate_web':
                # The whole batch owns one source/browser session. Child writing
                # jobs run sequentially and reuse it for body extraction and
                # fallback candidates, so Edge is not relaunched per article.
                return self.run_separate(job, config, selected, source)

            sources, hashes = [], set()
            for number, article_id in enumerate(selected, 1):
                self.check()
                article = self.db.rows("SELECT * FROM articles WHERE id=?", (article_id,))[0]
                if article['cleared'] and config.get('allow_recollect_cleared', False):
                    self.db.execute('UPDATE articles SET cleared=0 WHERE id=?', (article_id,))
                    article['cleared'] = 0
                if article['cleared']:
                    warn(f"{article['title']}：原素材已清理，无法继续处理")
                    continue
                self.notify(f"处理素材 {number}/{len(selected)}：{article['title']}")
                self.db.update_job(job["id"], stage="正文与摘要")
                try:
                    content = article["content"]
                    if not content:
                        content = source.content(article["url"])
                    image_analysis = '【图片识别资料（AI 转写' in content
                    if len(content) < 100:
                        images = self.db.article_images(article_id)
                        if not images:
                            raise ApiError('正文不足 100 字且未取得原文图片，无法可靠总结')
                        image_root = self.db.path.parent.resolve()
                        paths = []
                        fingerprints = []
                        for image in images:
                            path = (self.db.path.parent / image['local_path']).resolve()
                            if not path.is_relative_to(image_root) or not path.is_file():
                                continue
                            paths.append(path)
                            fingerprints.append([image['source_url'], path.stat().st_size])
                        if not paths:
                            raise ApiError('正文文字不足，已记录图片但本地文件不存在，请补采图片')
                        cache_key = digest(json.dumps(['image-reading-v1',
                            config['providers']['openai'], article['title'], content, fingerprints],
                            ensure_ascii=False, sort_keys=True))
                        cached = self.db.rows('SELECT result FROM analyses WHERE article_id=? AND cache_key=?',
                                              (article_id, cache_key))
                        if cached:
                            image_result = json.loads(cached[0]['result'])
                        else:
                            if not self.vault.get('openai'):
                                raise ApiError('图片型文章需要 OpenAI 图片识别，请先在设置中保存 OpenAI API Key')
                            self.notify(f"正文以图片为主，正在识别 {min(len(paths), 12)} 张原文图片…")
                            self.db.update_job(job['id'], stage='图片识别')
                            image_result = model('openai').analyze_article_images(article, paths, content)
                            self.db.execute('INSERT OR REPLACE INTO analyses VALUES(?,?,?,?)',
                                (article_id, cache_key, json.dumps(image_result, ensure_ascii=False), stamp()))
                            self.db.update_job(job['id'], stage='正文与摘要')
                        content = (content.strip() + '\n\n' + image_result['content']).strip()
                        image_analysis = True
                    self.db.execute("UPDATE articles SET content=?,content_hash=?,error='' WHERE id=?",
                                    (content, digest(content), article_id))
                    content_hash = digest(content)
                    if content_hash in hashes:
                        warn(f"{article['title']}：正文与其他素材相同，已去重")
                        continue
                    hashes.add(content_hash)
                    parts = []
                    # Cache each chunk separately so a retry never repeats completed summaries.
                    for start in range(0, len(content), 24000):
                        self.check()
                        chunk = content[start:start + 24000]
                        cache_key = digest(json.dumps(["summary-v1", config["summary_provider"],
                            config["providers"][config["summary_provider"]], chunk], ensure_ascii=False, sort_keys=True))
                        cached = self.db.rows("SELECT result FROM analyses WHERE article_id=? AND cache_key=?", (article_id, cache_key))
                        if cached:
                            part = json.loads(cached[0]["result"])
                        else:
                            part = summarizer.summary(article, chunk)
                            self.db.execute("INSERT OR REPLACE INTO analyses VALUES(?,?,?,?)",
                                            (article_id, cache_key, json.dumps(part, ensure_ascii=False), stamp()))
                        parts.append(part)
                    metrics = self.db.rows('SELECT reads,likes,observed_at FROM job_metrics WHERE job_id=? AND article_id=?', (job['id'], article_id))
                    sources.append({"source_id": f"S{article_id}", "article_id": article_id, "title": article["title"],
                                    "url": article["url"], "account_name": article["account_name"],
                                    "published_at": article["published_at"], "analysis": parts,
                                    "metrics": metrics[0] if metrics else {},
                                    "image_analysis": image_analysis})
                    self.db.execute("UPDATE articles SET error='' WHERE id=?", (article_id,))
                except ApiError as exc:
                    if exc.fatal:
                        raise
                    self.db.execute("UPDATE articles SET error=? WHERE id=?", (str(exc), article_id))
                    warn(f"{article['title']}：{exc}")
            if len(sources) < config["min_sources"]:
                detail = "最近错误：" + warnings[-1] if warnings else "请增加时间范围或调整最少素材数"
                raise ValueError(f"素材处理未完成：通过校验 {len(sources)} 篇，需要 {config['min_sources']} 篇；{detail}")
            if len(json.dumps(sources, ensure_ascii=False)) > 180000:
                raise ValueError("写作素材过长，请减少单次文章数量")
            self.check()
            if config.get('selection_mode') == 'hot' or config.get('writing_mode') == 'single_web':
                reference = sources[0]
                self.notify('正在分析最高阅读量有效素材的写作风格…')
                self.db.update_job(job['id'], stage='热点风格分析')
                content = self.db.rows('SELECT content FROM articles WHERE id=?', (reference['article_id'],))[0]['content'][:24000]
                cache_key = digest(json.dumps(['style-v2', config['summary_provider'], config['providers'][config['summary_provider']], reference['title'], content], sort_keys=True))
                cached = self.db.rows('SELECT result FROM analyses WHERE article_id=? AND cache_key=?', (reference['article_id'], cache_key))
                profile = json.loads(cached[0]['result']) if cached else summarizer.analyze_style(content, reference['title'])
                if not cached:
                    self.db.execute('INSERT OR REPLACE INTO analyses VALUES(?,?,?,?)',
                                    (reference['article_id'], cache_key, json.dumps(profile, ensure_ascii=False), stamp()))
                reference['style_reference'] = profile
            if config.get('comments_enabled', False):
                for reference in sources:
                    self.check()
                    self.db.update_job(job['id'], stage='评论分析')
                    self.notify('正在通过 EveryInfra 获取评论并分析读者关注点…')
                    aid = reference['article_id']
                    raw_key = 'comments-v1:' + str(job['id'])
                    try:
                        cached = self.db.rows('SELECT result FROM analyses WHERE article_id=? AND cache_key=?', (aid, raw_key))
                        if cached:
                            comments = json.loads(cached[0]['result'])
                        else:
                            comments = EveryInfra(self.vault.get('everyinfra'), transport).comments(reference['url'], config['comment_limit'])
                            self.db.execute('INSERT OR REPLACE INTO analyses VALUES(?,?,?,?)', (aid, raw_key, json.dumps(comments, ensure_ascii=False), stamp()))
                        key = digest(json.dumps(['comments-analysis-v1', config['summary_provider'], config['providers'][config['summary_provider']], comments], sort_keys=True))
                        cached = self.db.rows('SELECT result FROM analyses WHERE article_id=? AND cache_key=?', (aid, key))
                        analysis = json.loads(cached[0]['result']) if cached else (summarizer.analyze_comments(comments) if comments else {'insights': []})
                        if not cached:
                            self.db.execute('INSERT OR REPLACE INTO analyses VALUES(?,?,?,?)', (aid, key, json.dumps(analysis, ensure_ascii=False), stamp()))
                        reference['comment_analysis'] = {'status': '已分析' if comments else '接口未返回可见评论', 'sample_count': len(comments), **analysis}
                    except (ApiError, ValueError) as exc:
                        reference['comment_analysis'] = {'status': '获取或分析失败', 'reason': str(exc)}
                        warn('评论分析未完成：' + str(exc))
            research = None
            research_degraded = False
            if config.get('writing_mode') == 'single_web':
                self.db.update_job(job['id'], stage='联网研究')
                self.notify('正在联网补充该主题的信息并核对来源…')
                cached = self.db.rows('SELECT result FROM research_cache WHERE job_id=?', (job['id'],))
                if cached and json.loads(cached[0]['result']).get('provider') != 'openai':
                    cached = []
                try:
                    research = json.loads(cached[0]['result']) if cached else self.research_factory(self.vault.get('openai'), config, transport).search(sources[0])
                    research['provider'] = 'openai'
                    if not cached:
                        self.db.execute('INSERT OR REPLACE INTO research_cache VALUES(?,?)', (job['id'], json.dumps(research, ensure_ascii=False)))
                    sources[0]['web_research'] = research['notes']
                except ApiError as exc:
                    if exc.kind != 'network':
                        raise
                    research_degraded = True
                    config['research_degraded'] = True
                    sources[0]['web_research'] = '联网搜索连接失败或超过 300 秒，已降级为仅基于公众号原文写作。'
                    warn('联网搜索连接失败或超时，已降级为仅基于现有公众号内容生成；本稿未经联网补充核实')
            self.db.update_job(job["id"], stage="综合写作")
            self.notify("正在综合材料、组织大纲并生成正文…")
            writing_sources = sources + (research['sources'] if research else [])
            output = writer.write(writing_sources, config)
            quality_notes = []
            review = getattr(writer, 'review', None)
            if config.get('quality_review_enabled', True) and callable(review):
                self.check()
                self.db.update_job(job['id'], stage='质量二审')
                self.notify('正在对照证据复核事实边界、结构和表达…')
                try:
                    output = review(output, writing_sources, config)
                    quality_notes = output.pop('quality_notes', [])
                except ApiError as exc:
                    message = 'AI 二审未完成，已保留通过基础校验的初稿：' + str(exc)
                    output['review_notes'].append(message)
                    warn(message)
            self.check()
            report = "# 每日素材报告\n\n"
            if config.get('selection_mode') == 'hot':
                report += '选文规则：时间窗内按接口返回阅读量降序，点赞量及发布时间作为同阅读量时的排序依据。缺失阅读量不参与热点排名。仅代表已管理公众号及本轮查询范围。\n\n'
                report += f"风格参考：{sources[0]['title']}\n\n" + json.dumps(sources[0]['style_reference'], ensure_ascii=False, indent=2) + '\n\n'
            for s in sources:
                report += f"## [{s['source_id']}] {s['title']}\n公众号：{s['account_name']}\n原文：{s['url']}\n\n"
                metrics = s.get('metrics', {})
                report += f"阅读量：{metrics.get('reads', '未知')}；点赞量：{metrics.get('likes', '未知')}；数据采集时间：{metrics.get('observed_at', '未知')}\n\n"
                if 'comment_analysis' in s:
                    report += '评论样本分析（EveryInfra 提供评论，所选摘要模型分析；不代表总体民意）：\n' + json.dumps(s['comment_analysis'], ensure_ascii=False, indent=2) + '\n\n'
                if s.get('image_analysis'):
                    report += '素材说明：原文正文以图片为主，已使用 OpenAI 识别图片内容；转写可能有误，价格、日期、功效及专有名词需人工对照原图。\n\n'
                for part in s["analysis"]:
                    report += part["summary"] + "\n\n"
                    for fact in part["facts"]:
                        report += f"- {fact['claim']}\n  原文证据：{fact['quote']}\n"
                    for uncertainty in part["uncertainties"]:
                        report += f"- 待核实：{uncertainty}\n"
                report += "\n"
            report += "## 写作大纲\n" + "\n".join("- " + x for x in output["outline"])
            report += "\n\n## 审核提示\n" + "\n".join("- " + x for x in output["review_notes"])
            if quality_notes:
                report += "\n\n## AI 二审\n" + "\n".join("- " + x for x in quality_notes)
            report += "\n\n来源标记与引用片段已做格式校验，事实及文章观点仍需人工审核。\n"
            if research_degraded:
                report += '\n## 联网研究状态\n联网搜索连接失败或超过 300 秒，本稿已降级为仅基于公众号原文生成，未使用联网补充资料。\n'
            if research:
                report += '\n## 联网补充资料\n检索时间：' + research['retrieved_at'] + '\n\n' + research['notes'] + '\n\n'
                report += '\n'.join(f"[{s['source_id']}] {s['title']} — {s['url']}" for s in research['sources'])
                sources += research['sources']
            self.db.save_draft(job["id"], output["title"], output["body"], sources, report)
            status = "部分完成" if warnings else "已完成"
            self.db.update_job(job["id"], stage="完成", status=status, message=f"已生成 1 篇草稿，使用 {len(sources)} 篇素材，请人工审核")
            self.notify("稿件已保存，可在稿件工作台查看与编辑")
            return status
        except (Cancelled, ValueError, ApiError) as exc:
            if job:
                self.db.update_job(job["id"], status="已停止" if isinstance(exc, Cancelled) else "失败", message=str(exc))
            self.notify(str(exc))
            return "已停止" if isinstance(exc, Cancelled) else "失败"
        except Exception:
            if job:
                self.db.update_job(job["id"], status="失败", message="内部处理异常，数据已保留，请联系开发者检查")
            self.notify("内部处理异常，数据已保留，请联系开发者检查")
            raise
        finally:
            if owns_source and source is not None and hasattr(source, 'close'):
                source.close()
            self.lock.release()

    def run_separate(self, job, config, selected, source=None):
        # Each output owns a child job: a retry never rewrites completed outputs.
        from copy import deepcopy
        target = config.get('draft_count', 3)
        # v0.9.6 and earlier stored only the first N choices. Rebuild the ranked
        # backup queue from the parent job's captured metrics so retrying an old
        # partial task can also use the fourth article, without recollecting.
        selected = list(dict.fromkeys(selected))
        metric_rows = self.db.rows('''SELECT m.article_id,m.reads,m.likes,a.published_at
            FROM job_metrics m JOIN articles a ON a.id=m.article_id WHERE m.job_id=?''',
                                   (job['id'],))
        candidates = {}
        for row in metric_rows:
            try:
                candidates[row['article_id']] = {'reads': row['reads'], 'likes': row['likes'],
                                                  'published': parse_date(row['published_at'])}
            except (ValueError, TypeError, OverflowError, OSError):
                continue
        child_ids = {row['child_job_id'] for row in self.db.rows(
            'SELECT child_job_id FROM batch_items WHERE parent_job_id=?', (job['id'],))}
        used_elsewhere = set()
        for draft in self.db.rows('SELECT job_id,sources FROM drafts'):
            if draft['job_id'] in child_ids:
                continue
            used_elsewhere.update(item['article_id'] for item in json.loads(draft['sources'])
                                  if 'article_id' in item)
        for article_id in rank(candidates, 'hot', config.get('min_reads', 0)):
            if article_id not in selected and article_id not in used_elsewhere:
                selected.append(article_id)
        self.db.update_job(job['id'], selected=json.dumps(selected))
        primary_count = min(target, len(selected))
        done, failures, titles, replacement_slots = 0, [], [], 0
        replacements = []
        for rank_position, article_id in enumerate(selected, 1):
            is_replacement = rank_position > primary_count
            if is_replacement and (done >= target or replacement_slots <= 0):
                break
            self.check()
            child_config = deepcopy(config)
            child_config.update(writing_mode='single_web', selection_mode='hot', min_sources=1,
                                article_limit=1, topic='', avoid_titles=list(titles))
            child = self.db.job(f"batch:{job['id']}:{article_id}", child_config)
            self.db.execute('INSERT OR IGNORE INTO batch_items VALUES(?,?,?)', (job['id'], article_id, child['id']))
            self.db.update_job(child['id'], selected=json.dumps([article_id]))
            for metric in self.db.rows('SELECT * FROM job_metrics WHERE job_id=? AND article_id=?', (job['id'], article_id)):
                self.db.execute('INSERT OR IGNORE INTO job_metrics VALUES(?,?,?,?,?)',
                                (child['id'], article_id, metric['reads'], metric['likes'], metric['observed_at']))
            label = f'热度第 {rank_position} 篇'
            self.db.update_job(job['id'], stage=(f'补位写作：{label}' if is_replacement else
                                                 f'逐篇写作 {rank_position}/{primary_count}'))
            runner = Engine(self.db, self.vault, self.notify, self.source_factory, self.model_factory,
                            self.research_factory, self.profile_dir)
            runner.cancel = self.cancel
            runner.run(child['run_key'], child_config, reset_cancel=False,
                       shared_source=source)
            drafts = self.db.rows('SELECT title FROM drafts WHERE job_id=?', (child['id'],))
            if drafts:
                done += 1
                titles.append(drafts[0]['title'])
                if is_replacement:
                    replacement_slots -= 1
                    replacements.append(label)
            else:
                detail_row = self.db.rows('SELECT stage,message FROM jobs WHERE id=?', (child['id'],))[0]
                detail = detail_row['message']
                replaceable = (detail_row['stage'] == '正文与摘要'
                               and detail.startswith('素材处理未完成：'))
                failures.append(f'{label}：{detail}')
                if replaceable and not is_replacement:
                    replacement_slots += 1
                elif not replaceable and is_replacement:
                    # Do not walk the whole queue when a shared model, API-key,
                    # or research failure affects a replacement attempt.
                    replacement_slots -= 1
        status = '已完成' if done == target else ('部分完成' if done else '失败')
        message = f'已生成 {done}/{target} 篇独立主题文章，每篇分别联网研究和写作'
        if replacements:
            message += f'；{len(replacements)} 篇素材提取失败，已由' + '、'.join(replacements) + '补位'
        if len(selected) < target or (done < target and replacement_slots > 0):
            message += '；符合条件的新素材不足，请扩大时间范围后创建新任务'
        self.db.update_job(job['id'], status=status, stage='完成', message=message,
                           warnings=json.dumps(failures, ensure_ascii=False))
        self.notify(message)
        return status
