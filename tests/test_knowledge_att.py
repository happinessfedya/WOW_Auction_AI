import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wow_auction.knowledge_att import ATTError, extract_file, git_blob, preprocess, scan_corpus
from wow_auction.knowledge_sources import ATT_STANDARD, ATT_LICENSE_BLOB, dependency_disposition, fetch_corpus, include_path, resolve_latest_revision, source_policy


def extract(text, path=ATT_STANDARD + "test.lua"):
    return extract_file(text.encode(), path)


class ATTExtractionTests(unittest.TestCase):
    def test_leading_local_cost_constants_resolve_without_global_leak(self):
        shared={}
        row=extract_file(b'local GENESIS=188957; local LATTICE=187634; i(1,{_cost={{"i",GENESIS,250},{"i",LATTICE,1}}})',
                         ATT_STANDARD+'cost.lua',constants=shared)['candidates'][0]
        self.assertEqual(row['claim']['cost_claim'],[['i',188957,250],['i',187634,1]])
        self.assertEqual(shared,{})

    def test_reassigned_or_nonprefix_local_is_not_resolved(self):
        row=extract('local COIN=1; COIN=2; i(3,{cost={{"c",COIN,4}}})')['candidates'][0]
        self.assertIn('unresolved_expression',row['claim']['cost_claim'][0][1])
        row=extract('local COIN=1+2; i(3,{cost={{"c",COIN,4}}})')['candidates'][0]
        self.assertIn('unresolved_expression',row['claim']['cost_claim'][0][1])
        row=extract('print("x"); local COIN=1; i(3,{cost={{"c",COIN,4}}})')['candidates'][0]
        self.assertIn('unresolved_expression',row['claim']['cost_claim'][0][1])

    def test_profession_helper_recipe_item_is_not_output(self):
        row = extract('local i = GetRecipeHelperForProfession(ALCHEMY);\ni(123,456,"removed 8.0.1");',
                      ATT_STANDARD + '00 - Profession DB/Alchemy.lua')["candidates"][0]
        self.assertEqual(row["family"], "professional_recipe")
        self.assertEqual(row["claim"]["entity_id"], 456)
        self.assertEqual(row["claim"]["explicit_fields"]["recipeItemID"], 123)
        self.assertEqual(row["status"], "historical")
        self.assertIn("recipe_item_association_not_crafted_output", row["unknowns"])

    def test_container_nested_class_rewards_still_discovered(self):
        row = extract('container(1,{g={cl(3,{i(4)})}})')["candidates"][0]
        self.assertEqual(row["family"], "container_relation")
        self.assertEqual(row["claim"]["related_items"][0]["item_id"], 4)

    def test_cost_and_private_cost_are_claims_not_yields(self):
        result = extract('i(189363, { ["_cost"] = {{"i",188957,250},{"i",187634,1}}, ["g"]={ i(21), i(22) } })')
        row = result["candidates"][0]
        self.assertEqual(row["claim"]["cost_claim"], [["i",188957,250],["i",187634,1]])
        self.assertEqual(row["claim"]["cost_field"], "_cost")
        self.assertEqual([v["item_id"] for v in row["claim"]["related_items"]], [21,22])
        self.assertTrue(all(v["quantity"] is None for v in row["claim"]["related_items"]))
        self.assertIn("nested_collection_links_are_not_guaranteed_outputs", row["unknowns"])

    def test_classic_branch_excluded_without_erasing_retail_old_recipe(self):
        result = extract('-- #if ANYCLASSIC\nr(12)\n-- #else\nr(13)\n-- #endif\n-- #if AFTER TBC\nr(14)\n-- #endif\n')
        self.assertEqual([r["claim"]["entity_id"] for r in result["candidates"]], [13,14])
        self.assertEqual(result["preprocessor_regions"][0]["state"], "excluded")

    def test_unknown_condition_quarantined(self):
        result = extract('-- #if MYSTERY_FEATURE\nr(15)\n-- #endif\n')
        self.assertEqual(result["candidates"][0]["status"], "quarantined")
        self.assertIn("preprocessor_condition_unresolved", result["candidates"][0]["unknowns"])

    def test_preprocessor_is_case_insensitive_and_allows_space_after_hash(self):
        result=extract('-- # IF BEFORE 12.1.0\nr(1)\n-- # eLSe\nr(2)\n-- # endiF\n')
        self.assertEqual([r['claim']['entity_id'] for r in result['candidates']],[2])
        self.assertEqual(result['diagnostics'],[])

    def test_observed_att_default_closing_aliases_match_source_preprocessor(self):
        result=extract('-- #if BEFORE SL\nr(1)\n-- #end\nr(2)\n-- #if AFTER 12.1.7\nr(3)\n-- #endfif\nr(4)')
        self.assertEqual([row['claim']['entity_id'] for row in result['candidates']],[2,4])
        self.assertEqual(result['diagnostics'],[])

    def test_comments_and_long_strings_do_not_create_phantom_recipe(self):
        result = extract('-- r(1)\n--[=[ r(2) ]=]\nlocal x = [==[ r(3) ]==]\nr(4)')
        self.assertEqual([r["claim"]["entity_id"] for r in result["candidates"]], [4])

    def test_parent_removal_cannot_be_overridden_by_child_addition(self):
        result = extract('n(1, {timeline={"removed 12.1.0"}, g={r(2,{timeline={"added 12.1.1"}})}})')
        self.assertEqual(result["candidates"][0]["status"], "historical")

    def test_future_recipe_not_current(self):
        row = extract('r(2,{timeline={"added 13.0.0"}})')["candidates"][0]
        self.assertEqual(row["applicability"]["state"], "future")
        self.assertEqual(row["status"], "quarantined")

    def test_symbolic_container_keeps_reference(self):
        row = extract('i(257023, {sym=PREYSEEKER_BOX_SYM_SEASON_ONE})')["candidates"][0]
        self.assertEqual(row["claim"]["explicit_fields"]["sym"]["unresolved_expression"], "PREYSEEKER_BOX_SYM_SEASON_ONE")
        self.assertIn("symbolic_reference_unresolved", row["unknowns"])

    def test_unknown_constructor_branch_not_omitted(self):
        rows = extract('new_machine(200,{i(3),i(4)})')["candidates"]
        self.assertEqual(rows[0]["family"], "unknown_constructor")

    def test_variant_not_rounded_to_integer_or_float(self):
        row = extract('n(1,{i(123.456,{cost={{"g",500}}})})')["candidates"][0]
        self.assertEqual(row["claim"]["entity_id"], {"source_number":"123.456"})
        self.assertEqual(row["status"], "quarantined")

    def test_cost_does_not_leak_from_child_to_parent(self):
        rows = extract('i(1,{g={i(2,{cost={{"c",77,99}}})}})')["candidates"]
        self.assertIsNone(rows[0]["claim"]["cost_claim"])
        self.assertEqual(rows[1]["claim"]["cost_claim"], [["c",77,99]])

    def test_periodic_flag_is_not_economic_quota(self):
        row = extract('q(9,{isWeekly=true})')["candidates"][0]
        self.assertEqual(row["family"], "periodic_candidate")
        self.assertEqual(row["claim"]["explicit_fields"]["isWeekly"], True)
        self.assertNotIn("quota", row["claim"])

    def test_keys_ignore_comment_and_whitespace_shifts(self):
        first = extract('n(1,{r(2)})')["candidates"][0]
        second = extract('-- added comment\n\nn(1, {\n r(2)\n})')["candidates"][0]
        self.assertEqual(first["key"], second["key"])
        self.assertNotEqual(first["source"]["line"], second["source"]["line"])

    def test_malformed_or_excess_depth_is_explicit(self):
        with self.assertRaises(ATTError):
            extract('r(1,{')
        with self.assertRaises(ATTError):
            extract('{' * 130 + '}' * 130)


class ATTCorpusTests(unittest.TestCase):
    def test_new_revision_reuses_verified_blobs_and_downloads_only_changed(self):
        notice=b'synthetic license';revision='a'*40;tree_sha='b'*40
        names={'LICENSE':notice,ATT_STANDARD+'same.lua':b'r(1)',ATT_STANDARD+'changed.lua':b'r(3)'}
        requests=[]
        def fake(url,limit):
            requests.append(url)
            if '/git/commits/' in url:return json.dumps({'sha':revision,'tree':{'sha':tree_sha}}).encode()
            if '/git/trees/' in url:return json.dumps({'sha':tree_sha,'truncated':False,'tree':[
                {'path':name,'type':'blob','sha':git_blob(body),'size':len(body)} for name,body in names.items()]}).encode()
            return b'r(3)'
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp, patch('wow_auction.knowledge_sources.ATT_LICENSE_BLOB',git_blob(notice)):
            old=Path(tmp)/'old';new=Path(tmp)/'new'
            for name,body in names.items():
                p=old/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'r(2)' if name.endswith('changed.lua') else body)
            result=fetch_corpus(new,revision,1,fetch=fake,reuse_root=old)
            self.assertTrue(result['complete']);self.assertEqual(result['reused'],2);self.assertEqual(result['downloaded'],1)
            raws=[url for url in requests if 'raw.githubusercontent' in url]
            self.assertEqual(len(raws),1);self.assertTrue(raws[0].endswith('/changed.lua'))
            self.assertEqual((old/(ATT_STANDARD+'changed.lua')).read_bytes(),b'r(2)')

    def test_non_pinned_corpus_cannot_be_relabelled(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp);self.make_corpus(root,{ATT_STANDARD+'x.lua':b'r(1)'})
            with self.assertRaisesRegex(ATTError,'source_commit_tree_mismatch'):
                scan_corpus(root,'different-revision')
            (root/'commit.json').unlink()
            with self.assertRaisesRegex(ATTError,'source_commit_proof_missing'):
                scan_corpus(root,'synthetic')

    def test_cross_file_timeline_and_symbol_definition_are_evidence_not_pmf(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp)
            self.make_corpus(root,{
                '.contrib/.db/shared/constants/timelines.lua':b'REMOVED_X = "removed 12.1.0";',
                ATT_STANDARD+'old.lua':b'local BOX_LINK = {{"select","itemID",9}}\nn(1,{timeline={REMOVED_X},g={i(2,{sym=BOX_LINK})}})'
            })
            result=scan_corpus(root,'synthetic')
            row=next(c for c in result['candidates'] if c['claim']['entity_kind']=='item')
            self.assertEqual(row['status'],'historical')
            self.assertEqual(row['claim']['symbol_reference']['status'],'definition_located_semantics_unresolved')
            self.assertIn('joint_outcomes',row['unknowns'])

    def test_update_resolves_new_pin_and_fetch_resume_verifies_hashes(self):
        revision, tree_sha = 'a'*40, 'b'*40
        body=b'r(12)'; source_path=ATT_STANDARD+'new-old-expansion.lua'
        notice=b'synthetic license notice'
        calls=[]
        def fake(url, limit):
            calls.append(url)
            if '/commits/master' in url:
                return json.dumps({'sha':revision,'commit':{'tree':{'sha':tree_sha}}}).encode()
            if '/git/commits/' in url:
                return json.dumps({'sha':revision,'tree':{'sha':tree_sha}}).encode()
            if '/git/trees/' in url:
                return json.dumps({'sha':tree_sha,'truncated':False,'tree':[
                    {'path':source_path,'type':'blob','sha':git_blob(body),'size':len(body)}, {'path':'LICENSE','type':'blob','sha':git_blob(notice),'size':len(notice)}]}).encode()
            self.assertIn('/'+revision+'/',url)
            return notice if url.endswith('/LICENSE') else body
        self.assertEqual(resolve_latest_revision(fetch=fake),{'revision':revision,'tree':tree_sha})
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp, patch('wow_auction.knowledge_sources.ATT_LICENSE_BLOB',git_blob(notice)):
            first=fetch_corpus(Path(tmp),revision,workers=1,fetch=fake)
            self.assertTrue(first['complete']);self.assertEqual(first['downloaded'],2)
            calls.clear()
            second=fetch_corpus(Path(tmp),revision,workers=1,fetch=fake)
            self.assertEqual(second['cached'],2)
            self.assertFalse(any('raw.githubusercontent' in url for url in calls))
            scanned=scan_corpus(Path(tmp),revision)
            self.assertEqual(scanned['source']['revision'],revision)
            self.assertEqual(len(scanned['candidates']),1)

    def test_wrong_response_is_not_published_or_reported_complete(self):
        revision,tree_sha='a'*40,'b'*40
        def fake(url,limit):
            if '/git/commits/' in url: return json.dumps({'sha':revision,'tree':{'sha':tree_sha}}).encode()
            if '/git/trees/' in url: return json.dumps({'sha':tree_sha,'truncated':False,'tree':[
                {'path':ATT_STANDARD+'x.lua','type':'blob','sha':git_blob(b'r(1)'),'size':4}, {'path':'LICENSE','type':'blob','sha':ATT_LICENSE_BLOB,'size':20}]}).encode()
            return b'r(2)'
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp, patch('time.sleep'):
            result=fetch_corpus(Path(tmp),revision,workers=1,fetch=fake)
            self.assertFalse(result['complete']);self.assertEqual(len(result['errors']),2)
            self.assertFalse((Path(tmp)/(ATT_STANDARD+'x.lua')).exists())

    def make_corpus(self, root, rows):
        entries = []
        for path, data in rows.items():
            target = root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
            entries.append({"path":path,"type":"blob","sha":git_blob(data),"size":len(data)})
        (root/"tree.json").write_text(json.dumps({"sha":"synthetic-tree","truncated":False,"tree":entries}))
        (root/"commit.json").write_text(json.dumps({"sha":"synthetic","tree":{"sha":"synthetic-tree"}}))

    def test_manifest_conservation_unsupported_missing_resume_new_branch(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp)
            rows = {ATT_STANDARD + "old-expansion.lua":b"r(1)", ATT_STANDARD + "new-family.lua":b"alien(5,{i(6)})",
                    ATT_STANDARD + "malformed.lua":b"r(8,{", "LICENSE":b"synthetic notice"}
            self.make_corpus(root, rows)
            (root/(ATT_STANDARD + "old-expansion.lua")).unlink()
            before = scan_corpus(root, "synthetic")
            self.assertFalse(before["complete"])
            self.assertEqual(before["denominator"], 4)
            self.assertEqual(sum(before["coverage"]["file_dispositions"].values()), 4)
            (root/(ATT_STANDARD + "old-expansion.lua")).write_bytes(rows[ATT_STANDARD + "old-expansion.lua"])
            after = scan_corpus(root, "synthetic")
            self.assertTrue(after["complete"])
            self.assertEqual(len(after["candidates"]), 2)
            self.assertEqual(after["coverage"]["file_dispositions"]["unsupported"], 1)
            self.assertFalse(after["mechanics_complete"])

    def test_corrupt_payload_not_accepted_as_resume(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp); self.make_corpus(root,{ATT_STANDARD+"x.lua":b"r(1)"})
            (root/(ATT_STANDARD+"x.lua")).write_bytes(b"r(2)")
            result=scan_corpus(root,"synthetic")
            self.assertFalse(result["complete"])
            self.assertEqual(result["files"][0]["disposition"], "hash_mismatch")
            self.assertEqual(result["candidates"], [])

    def test_truncated_tree_and_parent_traversal_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp)
            (root/"tree.json").write_text(json.dumps({"truncated":True,"tree":[]}))
            with self.assertRaises(ATTError): scan_corpus(root,"synthetic")
            self.make_corpus(root,{})
            tree={"truncated":False,"tree":[{"path":ATT_STANDARD+"../../escape.lua","type":"blob","sha":"bad"}]}
            (root/"tree.json").write_text(json.dumps(tree))
            with self.assertRaises(ATTError): scan_corpus(root,"synthetic")

    def test_upstream_data_and_blizzard_are_explicitly_excluded(self):
        self.assertFalse(include_path(ATT_STANDARD+".config/.wago/ItemEffect.csv"))
        self.assertTrue(include_path(ATT_STANDARD+"unknown-old-system.lua"))
        self.assertEqual(next(row for row in source_policy()["blocked"] if row['source_id']=='att-wago-item-links')["disposition"],"license_blocked")
        self.assertEqual(source_policy()["blocked"][-1]["disposition"],"disabled_by_user")

    def test_required_json_dependencies_remain_in_scope_with_rights_gap(self):
        for name in ['itemDB.json','questDB.json']:
            row=dependency_disposition(ATT_STANDARD+'00 - Item DB/'+name)
            self.assertEqual(row['disposition'],'rights_provenance_blocked')
            self.assertTrue(row['required_dependency'])
            self.assertEqual(row['dependency_role'],'Parser.RawJsonMerge_before_Lua')
        self.assertEqual(dependency_disposition(ATT_STANDARD+'00 - Missing DB/12 - Retail/Professions/Alchemy.txt')['disposition'],'rights_provenance_blocked')
        self.assertEqual(dependency_disposition(ATT_STANDARD+'00 - Item DB/Conduits/class_info.py')['disposition'],'generator_not_executed')


if __name__ == "__main__":
    unittest.main()
