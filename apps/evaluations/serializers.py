from rest_framework import serializers

from .models import (
    TipoExamen,
    Evaluacion,
    RubricaEvaluacion,
    CriterioRubrica,
    EntregaEvaluacion,
    CorreccionEvaluacion,
)


class TipoExamenSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipoExamen
        fields = ['id', 'nombre', 'codigo', 'descripcion', 'activo']


class CriterioRubricaSerializer(serializers.ModelSerializer):
    class Meta:
        model = CriterioRubrica
        fields = [
            'id',
            'codigo',
            'nombre',
            'descripcion',
            'peso',
            'escala_min',
            'escala_max',
            'obligatorio',
            'orden',
        ]


class RubricaEvaluacionSerializer(serializers.ModelSerializer):
    criterios = CriterioRubricaSerializer(many=True)

    class Meta:
        model = RubricaEvaluacion
        fields = ['id', 'version', 'nota_maxima', 'umbral_aprobado', 'criterios']


class EvaluacionSerializer(serializers.ModelSerializer):
    rubrica = RubricaEvaluacionSerializer(required=False)
    tipo_examen_detalle = TipoExamenSerializer(source='tipo_examen', read_only=True)

    class Meta:
        model = Evaluacion
        fields = [
            'id',
            'aula',
            'tipo_prueba',
            'tipo_examen',
            'tipo_examen_detalle',
            'titulo',
            'alcance_tipo',
            'tema',
            'curso',
            'cursos',
            'modulo_ref',
            'enunciado',
            'enunciado_pdf',
            'criterios_evaluacion_pdf',
            'rubrica_pdf',
            'instrucciones',
            'criterios_a_valorar',
            'max_puntuacion',
            'fecha_prueba',
            'fecha_apertura',
            'fecha_cierre',
            'estado',
            'rubrica',
            'creada_en',
            'actualizada_en',
        ]
        read_only_fields = ['creada_en', 'actualizada_en']

    def validate(self, attrs):
        alcance = attrs.get('alcance_tipo', getattr(self.instance, 'alcance_tipo', None))
        aula = attrs.get('aula', getattr(self.instance, 'aula', None))
        tema = attrs.get('tema', getattr(self.instance, 'tema', None))
        curso = attrs.get('curso', getattr(self.instance, 'curso', None))
        cursos = attrs.get('cursos', None)

        if not aula:
            raise serializers.ValidationError({'aula': 'La evaluacion debe asignarse a un aula.'})

        if alcance == Evaluacion.ALCANCE_UD and not tema:
            raise serializers.ValidationError({'tema': 'UD requiere seleccionar un tema.'})
        if alcance == Evaluacion.ALCANCE_UF and not curso:
            raise serializers.ValidationError({'curso': 'UF requiere seleccionar un curso.'})

        if self.instance and alcance == Evaluacion.ALCANCE_MF and cursos is None:
            cursos = self.instance.cursos.all()
        if alcance == Evaluacion.ALCANCE_MF and not cursos:
            raise serializers.ValidationError({'cursos': 'MF requiere seleccionar uno o varios cursos.'})

        return attrs

    def create(self, validated_data):
        rubrica_data = validated_data.pop('rubrica', None)
        evaluacion = Evaluacion.objects.create(**validated_data)
        if rubrica_data:
            criterios_data = rubrica_data.pop('criterios', [])
            rubrica = RubricaEvaluacion.objects.create(evaluacion=evaluacion, **rubrica_data)
            CriterioRubrica.objects.bulk_create([
                CriterioRubrica(rubrica=rubrica, **criterio) for criterio in criterios_data
            ])
        return evaluacion

    def update(self, instance, validated_data):
        rubrica_data = validated_data.pop('rubrica', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if rubrica_data is not None:
            criterios_data = rubrica_data.pop('criterios', [])
            rubrica, _ = RubricaEvaluacion.objects.get_or_create(evaluacion=instance)
            for attr, value in rubrica_data.items():
                setattr(rubrica, attr, value)
            rubrica.save()
            rubrica.criterios.all().delete()
            CriterioRubrica.objects.bulk_create([
                CriterioRubrica(rubrica=rubrica, **criterio) for criterio in criterios_data
            ])

        return instance


class CorreccionEvaluacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CorreccionEvaluacion
        fields = [
            'id',
            'puntuacion_total',
            'puntuacion_por_criterio',
            'feedback_global',
            'feedback_detallado',
            'evidencias',
            'plan_mejora',
            'confianza_modelo',
            'modelo_ia',
            'prompt_version',
            'requiere_revision_humana',
            'tipo_correccion',
            'revisado_por',
            'observaciones_docente',
            'fecha_revision_docente',
            'creada_en',
        ]


class EntregaEvaluacionSerializer(serializers.ModelSerializer):
    correccion = CorreccionEvaluacionSerializer(read_only=True)

    class Meta:
        model = EntregaEvaluacion
        fields = [
            'id',
            'evaluacion',
            'alumno',
            'archivo_respuesta',
            'texto_extraido',
            'estado',
            'solicita_revision_exhaustiva',
            'motivo_revision_exhaustiva',
            'hash_archivo',
            'intento_numero',
            'fecha_entrega',
            'procesada_en',
            'correccion',
        ]
        read_only_fields = [
            'alumno',
            'texto_extraido',
            'estado',
            'solicita_revision_exhaustiva',
            'motivo_revision_exhaustiva',
            'hash_archivo',
            'intento_numero',
            'fecha_entrega',
            'procesada_en',
            'correccion',
        ]


class EntregaCreateSerializer(serializers.ModelSerializer):
    solicita_revision_exhaustiva = serializers.BooleanField(required=False, default=False)
    motivo_revision_exhaustiva = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = EntregaEvaluacion
        fields = ['archivo_respuesta', 'solicita_revision_exhaustiva', 'motivo_revision_exhaustiva']
