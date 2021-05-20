from ufo2ft.featureWriters import BaseFeatureWriter
from types import SimpleNamespace
from fontTools.feaLib.variableScalar import VariableScalar
from fontTools.feaLib import ast
from collections import OrderedDict, defaultdict


class VariableRulesWriter(BaseFeatureWriter):
    def write(self, font, feaFile, compiler=None):
        """Write features and class definitions for this font to a feaLib
        FeatureFile object.
        Returns True if feature file was modified, False if no new features
        were generated.
        """
        self.setContext(font, feaFile, compiler=compiler)
        return self._write()

    def _write(self):
        self._designspace = self.context.font
        self._axis_map = {axis.name: axis.tag for axis in self._designspace.axes}

        feaFile = self.context.feaFile
        self._conditionsets = []
        for r in self._designspace.rules:
            conditionsets = [self.rearrangeConditionSet(c) for c in r.conditionSets]
            for conditionset in conditionsets:
                if conditionset not in self._conditionsets:
                    cs_name = "ConditionSet%i" % (len(self._conditionsets) + 1)
                    feaFile.statements.append(
                        ast.ConditionsetStatement(cs_name, conditionset)
                    )
                    self._conditionsets.append(conditionset)
                else:
                    cs_name = "ConditionSet%i" % self._conditionsets.index(conditionset)
                block = ast.VariationBlock("rvrn", cs_name)
                for sub in r.subs:
                    block.statements.append(
                        ast.SingleSubstStatement(
                            [ast.GlyphName(sub[0])],
                            [ast.GlyphName(sub[1])],
                            [],
                            [],
                            False,
                        )
                    )
                feaFile.statements.append(block)

    def rearrangeConditionSet(self, condition):
        return {
            self._axis_map[rule["name"]]: (rule["minimum"], rule["maximum"])
            for rule in condition
        }
