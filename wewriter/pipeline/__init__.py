"""Stages of the collection and writing pipeline.

``wewriter.engine.Engine`` orchestrates a job; the stage logic lives here:

    collect      account listing, time window, ranking, collection funnel
    materials    body text, image reading, de-duplication, chunked summaries
    research     comment analysis and web research
    composition  style reference, writing, quality review, report, rewrite

Stage functions take the ``Engine`` as their first argument and call sibling
stages through its methods, so patching an ``Engine`` method still takes effect.
"""
